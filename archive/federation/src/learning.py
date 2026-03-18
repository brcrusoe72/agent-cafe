"""
Agent Café — Federated Scrubber Learning
Continuous model improvement through federation.

When an agent is killed anywhere in the network:
1. The killing node extracts the attack text + classification
2. The sample (sanitized) propagates to the hub
3. Hub aggregates samples from all nodes
4. Nodes periodically pull new training data from hub
5. Each node retrains its local classifier with the combined dataset

This is NOT federated learning in the ML sense (no gradient sharing).
This is federated DATA collection — samples travel, models train locally.
The classifier stays local. The training data is shared.

Why not gradient sharing?
- Our model is tiny (TF-IDF + LogReg, ~1ms inference)
- Retraining from scratch takes <1 second
- Samples are more valuable than gradients for this size model
- Simpler = more auditable = more trustworthy
"""

import json
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from pathlib import Path

try:
    from ..db import get_db
except ImportError:
    from db import get_db

from .protocol import MessageType


# ═══════════════════════════════════════════════════════════════
# Federated Sample Store
# ═══════════════════════════════════════════════════════════════

def init_learning_tables():
    """Create tables for federated learning data."""
    with get_db() as conn:
        # Shared training samples (from kills across the network)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS federated_samples (
                sample_id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                text_hash TEXT NOT NULL UNIQUE,
                label INTEGER NOT NULL,
                source_node TEXT NOT NULL,
                source_type TEXT NOT NULL,
                threat_type TEXT,
                confidence REAL DEFAULT 1.0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                used_in_training INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_fed_samples_hash
            ON federated_samples(text_hash)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_fed_samples_source
            ON federated_samples(source_node)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_fed_samples_trained
            ON federated_samples(used_in_training)
        """)
        
        # Model version tracking
        conn.execute("""
            CREATE TABLE IF NOT EXISTS model_versions (
                version_id TEXT PRIMARY KEY,
                trained_at TIMESTAMP NOT NULL,
                sample_count INTEGER NOT NULL,
                local_samples INTEGER NOT NULL,
                federated_samples INTEGER NOT NULL,
                accuracy REAL,
                injection_f1 REAL,
                legit_f1 REAL,
                cv_accuracy REAL,
                notes TEXT DEFAULT ''
            )
        """)
        
        conn.commit()


class FederatedLearning:
    """
    Federated learning coordinator.
    
    Collects training samples from kills (local and remote),
    merges with existing training data, and retrains the classifier.
    
    The model is always local. Only samples travel.
    """
    
    def __init__(self):
        self._initialized = False
        self._classifier_data_path = Path(__file__).parent.parent / "layers" / "classifier_data.json"
    
    def initialize(self):
        if self._initialized:
            return
        init_learning_tables()
        self._initialized = True
    
    # ═══════════════════════════════════════════════════════════
    # Sample Collection
    # ═══════════════════════════════════════════════════════════
    
    def collect_from_kill(
        self,
        text: str,
        threat_type: str,
        agent_id: str,
        confidence: float = 1.0,
        source_type: str = "local_kill"
    ) -> Optional[str]:
        """
        Collect a training sample from a local kill.
        
        Called by the immune system when an agent is terminated.
        The attack text becomes a positive (injection) training sample.
        
        Args:
            text: The message that triggered the kill
            threat_type: Type of threat detected
            agent_id: The killed agent's ID
            confidence: How confident we are this is injection (0-1)
            source_type: "local_kill", "scrub_block", "operator_flag"
        
        Returns sample_id if new, None if duplicate.
        """
        if not self._initialized:
            self.initialize()
        
        # Sanitize: strip any actual sensitive data from the sample
        sanitized = self._sanitize_sample(text)
        if not sanitized or len(sanitized) < 10:
            return None
        
        text_hash = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
        sample_id = f"sample_{uuid.uuid4().hex[:16]}"
        
        try:
            with get_db() as conn:
                conn.execute("""
                    INSERT OR IGNORE INTO federated_samples
                    (sample_id, text, text_hash, label, source_node, source_type,
                     threat_type, confidence, created_at, ingested_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    sample_id, sanitized, text_hash, 1,  # label=1 for injection
                    "local", source_type, threat_type, confidence,
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat()
                ))
                
                if conn.total_changes > 0:
                    conn.commit()
                    return sample_id
                conn.commit()
                return None  # Duplicate
        except Exception as e:
            print(f"⚠️  Failed to collect sample: {e}")
            return None
    
    def collect_legitimate(
        self,
        text: str,
        source_type: str = "verified_clean"
    ) -> Optional[str]:
        """
        Collect a legitimate (non-injection) training sample.
        
        Called when a message passes scrubbing AND the job completes
        successfully — confirmed clean interaction.
        
        This is important for reducing false positives.
        """
        if not self._initialized:
            self.initialize()
        
        sanitized = self._sanitize_sample(text)
        if not sanitized or len(sanitized) < 10:
            return None
        
        text_hash = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
        sample_id = f"sample_{uuid.uuid4().hex[:16]}"
        
        try:
            with get_db() as conn:
                conn.execute("""
                    INSERT OR IGNORE INTO federated_samples
                    (sample_id, text, text_hash, label, source_node, source_type,
                     threat_type, confidence, created_at, ingested_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    sample_id, sanitized, text_hash, 0,  # label=0 for legit
                    "local", source_type, None, 1.0,
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat()
                ))
                
                if conn.total_changes > 0:
                    conn.commit()
                    return sample_id
                conn.commit()
                return None
        except Exception:
            return None
    
    # ═══════════════════════════════════════════════════════════
    # Federation Sharing
    # ═══════════════════════════════════════════════════════════
    
    def get_samples_for_sharing(
        self,
        since: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get local samples to share with the hub.
        
        Only shares samples collected locally — doesn't re-share
        samples received from other nodes.
        
        Args:
            since: ISO timestamp — only return samples newer than this
            limit: Max samples to return
        """
        if not self._initialized:
            self.initialize()
        
        with get_db() as conn:
            query = """
                SELECT sample_id, text, text_hash, label, source_type,
                       threat_type, confidence, created_at
                FROM federated_samples
                WHERE source_node = 'local'
            """
            params = []
            
            if since:
                query += " AND created_at > ?"
                params.append(since)
            
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            
            rows = conn.execute(query, params).fetchall()
            
            return [
                {
                    "sample_id": row["sample_id"],
                    "text": row["text"],
                    "text_hash": row["text_hash"],
                    "label": row["label"],
                    "source_type": row["source_type"],
                    "threat_type": row["threat_type"],
                    "confidence": row["confidence"],
                    "created_at": row["created_at"]
                }
                for row in rows
            ]
    
    def ingest_remote_samples(
        self,
        samples: List[Dict[str, Any]],
        source_node: str
    ) -> int:
        """
        Ingest training samples from a remote node (via hub).
        
        Deduplicates by text hash. Returns count of new samples ingested.
        """
        if not self._initialized:
            self.initialize()
        
        ingested = 0
        with get_db() as conn:
            for sample in samples:
                text = sample.get("text", "")
                if not text or len(text) < 10:
                    continue
                
                text_hash = sample.get("text_hash") or hashlib.sha256(text.encode("utf-8")).hexdigest()
                
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO federated_samples
                        (sample_id, text, text_hash, label, source_node, source_type,
                         threat_type, confidence, created_at, ingested_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        sample.get("sample_id", f"sample_{uuid.uuid4().hex[:16]}"),
                        text, text_hash,
                        sample.get("label", 1),
                        source_node,
                        sample.get("source_type", "federation"),
                        sample.get("threat_type"),
                        sample.get("confidence", 0.8),  # Slightly lower confidence for remote
                        sample.get("created_at", datetime.now(timezone.utc).isoformat()),
                        datetime.now(timezone.utc).isoformat()
                    ))
                    
                    if conn.total_changes > 0:
                        ingested += 1
                except Exception:
                    continue
            
            conn.commit()
        
        if ingested > 0:
            print(f"🧠 Ingested {ingested} training samples from {source_node}")
        
        return ingested
    
    # ═══════════════════════════════════════════════════════════
    # Model Retraining
    # ═══════════════════════════════════════════════════════════
    
    def retrain_classifier(self, min_new_samples: int = 5) -> Optional[Dict[str, Any]]:
        """
        Retrain the local classifier with all available data
        (local + federated samples).
        
        Only retrains if there are at least `min_new_samples` untrained samples.
        
        Returns training metrics or None if no retraining needed.
        """
        if not self._initialized:
            self.initialize()
        
        # Check for new untrained samples
        with get_db() as conn:
            untrained = conn.execute(
                "SELECT COUNT(*) FROM federated_samples WHERE used_in_training = 0"
            ).fetchone()[0]
            
            if untrained < min_new_samples:
                return None  # Not enough new data
        
        # Load ALL samples (existing classifier data + federated)
        all_texts = []
        all_labels = []
        
        # 1. Load existing classifier_data.json (the original training set)
        if self._classifier_data_path.exists():
            try:
                with open(self._classifier_data_path) as f:
                    original_data = json.load(f)
                for sample in original_data.get("samples", []):
                    all_texts.append(sample["text"])
                    all_labels.append(sample["label"])
            except Exception:
                pass
        
        local_count = len(all_texts)
        
        # 2. Load all federated samples
        with get_db() as conn:
            rows = conn.execute("""
                SELECT text, label, confidence FROM federated_samples
                ORDER BY confidence DESC
            """).fetchall()
            
            fed_count = 0
            for row in rows:
                # Weighted: high-confidence samples go in once,
                # lower confidence could be downweighted but we keep it simple
                if row["confidence"] >= 0.5:
                    all_texts.append(row["text"])
                    all_labels.append(row["label"])
                    fed_count += 1
        
        if len(all_texts) < 20:
            print("⚠️  Not enough total samples for training")
            return None
        
        # 3. Retrain classifier
        try:
            from layers.classifier import InjectionClassifier
            clf = InjectionClassifier.__new__(InjectionClassifier)
            clf.threshold = 0.5
            clf.pipeline = None
            metrics = clf.train(all_texts, all_labels)
        except Exception as e:
            print(f"⚠️  Classifier retraining failed: {e}")
            return None
        
        # 4. Mark all samples as trained
        with get_db() as conn:
            conn.execute("UPDATE federated_samples SET used_in_training = 1")
            conn.commit()
        
        # 5. Record model version
        version_id = f"model_{uuid.uuid4().hex[:12]}"
        with get_db() as conn:
            conn.execute("""
                INSERT INTO model_versions
                (version_id, trained_at, sample_count, local_samples, federated_samples,
                 accuracy, injection_f1, legit_f1, cv_accuracy, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                version_id,
                datetime.now(timezone.utc).isoformat(),
                len(all_texts),
                local_count,
                fed_count,
                metrics.get("train_accuracy"),
                metrics.get("injection_f1"),
                metrics.get("legit_f1"),
                metrics.get("cv_accuracy_mean"),
                f"Retrained with {untrained} new federated samples"
            ))
            conn.commit()
        
        # 6. Reload the global classifier instance
        try:
            from layers.classifier import get_classifier
            clf_instance = get_classifier()
            clf_instance._load_or_train()
        except Exception:
            pass
        
        print(f"🧠 Classifier retrained: {len(all_texts)} samples "
              f"({local_count} local + {fed_count} federated), "
              f"accuracy={metrics.get('cv_accuracy_mean', 0):.3f}")
        
        return {
            "version_id": version_id,
            "total_samples": len(all_texts),
            "local_samples": local_count,
            "federated_samples": fed_count,
            "new_samples": untrained,
            "metrics": metrics
        }
    
    # ═══════════════════════════════════════════════════════════
    # Auto-collection Hooks
    # ═══════════════════════════════════════════════════════════
    
    def on_scrub_block(self, message: str, threats: List[Dict], risk_score: float):
        """
        Hook: called when scrubber blocks a message.
        
        High-risk blocks become injection training samples.
        """
        if risk_score >= 0.7:
            threat_types = [t.get("threat_type", "unknown") for t in threats] if threats else ["unknown"]
            self.collect_from_kill(
                text=message,
                threat_type=threat_types[0] if threat_types else "unknown",
                agent_id="scrubber",
                confidence=min(risk_score, 1.0),
                source_type="scrub_block"
            )
    
    def on_job_complete(self, messages: List[str]):
        """
        Hook: called when a job completes successfully.
        
        Messages from completed jobs are confirmed legitimate samples.
        Only collects a random subset to avoid overwhelming with legit data.
        """
        import random
        # Collect ~20% of messages from successful jobs
        for msg in messages:
            if random.random() < 0.2 and len(msg) > 20:
                self.collect_legitimate(msg, source_type="completed_job")
    
    def on_agent_kill(self, agent_id: str, evidence_messages: List[str], 
                      threat_types: List[str]):
        """
        Hook: called when an agent is killed.
        
        All evidence messages become high-confidence injection samples.
        """
        for i, msg in enumerate(evidence_messages):
            threat_type = threat_types[i] if i < len(threat_types) else "unknown"
            self.collect_from_kill(
                text=msg,
                threat_type=threat_type,
                agent_id=agent_id,
                confidence=1.0,
                source_type="local_kill"
            )
    
    # ═══════════════════════════════════════════════════════════
    # Sanitization
    # ═══════════════════════════════════════════════════════════
    
    def _sanitize_sample(self, text: str) -> str:
        """
        Sanitize a training sample before storage/sharing.
        
        Removes:
        - Actual API keys, tokens, passwords
        - Email addresses (replaced with placeholder)
        - URLs (replaced with placeholder)
        - Very long texts (truncated to 2000 chars)
        
        Keeps:
        - The attack pattern/structure
        - Injection keywords and techniques
        - Enough context for the classifier to learn
        """
        import re
        
        sanitized = text
        
        # Remove things that look like real API keys
        sanitized = re.sub(r'[a-zA-Z0-9_-]{32,}', '<TOKEN>', sanitized)
        
        # Remove email addresses
        sanitized = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', '<EMAIL>', sanitized)
        
        # Remove URLs (but keep the injection pattern around them)
        sanitized = re.sub(r'https?://[^\s<>"\']+', '<URL>', sanitized)
        
        # Truncate
        if len(sanitized) > 2000:
            sanitized = sanitized[:2000]
        
        return sanitized.strip()
    
    # ═══════════════════════════════════════════════════════════
    # Stats
    # ═══════════════════════════════════════════════════════════
    
    def stats(self) -> Dict[str, Any]:
        """Get federated learning statistics."""
        if not self._initialized:
            self.initialize()
        
        with get_db() as conn:
            total = conn.execute("SELECT COUNT(*) FROM federated_samples").fetchone()[0]
            injection = conn.execute(
                "SELECT COUNT(*) FROM federated_samples WHERE label = 1"
            ).fetchone()[0]
            legit = conn.execute(
                "SELECT COUNT(*) FROM federated_samples WHERE label = 0"
            ).fetchone()[0]
            untrained = conn.execute(
                "SELECT COUNT(*) FROM federated_samples WHERE used_in_training = 0"
            ).fetchone()[0]
            
            by_source = conn.execute("""
                SELECT source_node, COUNT(*) as cnt
                FROM federated_samples GROUP BY source_node
            """).fetchall()
            
            by_type = conn.execute("""
                SELECT threat_type, COUNT(*) as cnt
                FROM federated_samples WHERE label = 1
                GROUP BY threat_type ORDER BY cnt DESC
            """).fetchall()
            
            # Latest model version
            latest_model = conn.execute("""
                SELECT * FROM model_versions ORDER BY trained_at DESC LIMIT 1
            """).fetchone()
            
            return {
                "total_samples": total,
                "injection_samples": injection,
                "legit_samples": legit,
                "untrained_samples": untrained,
                "by_source": {row["source_node"]: row["cnt"] for row in by_source},
                "by_threat_type": {row["threat_type"]: row["cnt"] for row in by_type},
                "latest_model": dict(latest_model) if latest_model else None
            }
    
    def model_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get model version history."""
        if not self._initialized:
            self.initialize()
        
        with get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM model_versions ORDER BY trained_at DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(row) for row in rows]


# ═══════════════════════════════════════════════════════════════
# Federation Protocol Extensions
# ═══════════════════════════════════════════════════════════════

# New message types for learning sync (add to protocol.py schema)
LEARNING_MESSAGE_TYPES = {
    "learning.samples_share": ["samples"],
    "learning.samples_request": [],
    "learning.samples_response": ["samples", "total_available"],
    "learning.model_stats": ["sample_count", "accuracy", "version"],
}


# ═══════════════════════════════════════════════════════════════
# Global Singleton
# ═══════════════════════════════════════════════════════════════

federated_learning = FederatedLearning()
