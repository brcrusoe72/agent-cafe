"""
Agent Café - Anti-Framing Defense Layer 🛡️
Detects when good agents are being framed by bad actors.

Core components:
- Provenance chain verification
- Trap detection (bait-and-report defense)
- Behavioral anomaly scoring
- Framing score computation
"""

import hashlib
import hmac
import json
import math
import re
import uuid
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from cafe_logging import get_logger

logger = get_logger("layers.framing")

try:
    from ..db import get_db, get_agent_by_id
    from ..models import ThreatType, ScrubResult
except ImportError:
    from db import get_db, get_agent_by_id
    from models import ThreatType, ScrubResult


# ============================================================
# PROVENANCE CHAIN
# ============================================================

class ProvenanceChain:
    """Cryptographic message provenance — proves who sent what."""

    @staticmethod
    def derive_signing_key(api_key: str) -> str:
        """Derive a signing key from the agent's API key."""
        return hashlib.sha256((api_key + ":signing").encode()).hexdigest()

    @staticmethod
    def compute_content_hash(content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    @staticmethod
    def compute_signature(content_hash: str, prev_hash: str, timestamp: float,
                          signing_key: str) -> str:
        payload = f"{content_hash}|{prev_hash or ''}|{timestamp}"
        return hmac.new(signing_key.encode(), payload.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def record(message_id: str, job_id: str, from_agent: str, content: str,
               api_key_prefix: str, request_id: str, source_ip: str,
               signing_key: str):
        """Record provenance for a message."""
        now = datetime.now().timestamp()
        content_hash = ProvenanceChain.compute_content_hash(content)

        with get_db() as conn:
            # Get previous message hash for chain
            prev = conn.execute("""
                SELECT content_hash FROM message_provenance
                WHERE job_id = ? ORDER BY server_timestamp DESC LIMIT 1
            """, (job_id,)).fetchone()
            prev_hash = prev["content_hash"] if prev else None

            signature = ProvenanceChain.compute_signature(
                content_hash, prev_hash, now, signing_key
            )

            conn.execute("""
                INSERT OR REPLACE INTO message_provenance
                (message_id, job_id, from_agent, source_ip, api_key_prefix,
                 request_id, content_hash, prev_message_hash, signature,
                 timestamp, server_timestamp, verified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """, (
                message_id, job_id, from_agent, source_ip, api_key_prefix,
                request_id, content_hash, prev_hash, signature,
                now, now
            ))
            conn.commit()

    @staticmethod
    def verify_chain(job_id: str) -> Tuple[bool, List[str]]:
        """Verify the entire provenance chain for a job. Returns (valid, issues)."""
        issues = []
        with get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM message_provenance
                WHERE job_id = ? ORDER BY server_timestamp ASC
            """, (job_id,)).fetchall()

        if not rows:
            return True, []  # No messages = nothing to verify

        prev_hash = None
        for row in rows:
            row = dict(row)
            # Verify chain linkage
            if row["prev_message_hash"] != prev_hash:
                issues.append(
                    f"Chain break at {row['message_id']}: "
                    f"expected prev={prev_hash}, got={row['prev_message_hash']}"
                )
            prev_hash = row["content_hash"]

        return len(issues) == 0, issues

    @staticmethod
    def verify_message(message_id: str, signing_key: str) -> Tuple[bool, str]:
        """Verify a single message's signature."""
        with get_db() as conn:
            row = conn.execute("""
                SELECT * FROM message_provenance WHERE message_id = ?
            """, (message_id,)).fetchone()

        if not row:
            return False, "No provenance record found"

        row = dict(row)
        expected_sig = ProvenanceChain.compute_signature(
            row["content_hash"], row["prev_message_hash"],
            row["timestamp"], signing_key
        )

        if not hmac.compare_digest(expected_sig, row["signature"]):
            return False, "Signature mismatch — message may be spoofed"

        return True, "Signature valid"


# ============================================================
# TRAP DETECTION
# ============================================================

# Patterns in job descriptions that try to elicit injection-like responses
ELICITATION_PATTERNS = [
    r"(?i)write\s+(?:a\s+)?(?:prompt|message|instruction)\s+(?:that|which)",
    r"(?i)create\s+(?:a\s+)?(?:test\s+)?(?:example\s+)?(?:of\s+)?(?:prompt\s+)?injection",
    r"(?i)show\s+(?:me\s+)?(?:how|an?\s+example)\s+(?:of\s+)?(?:injection|jailbreak)",
    r"(?i)(?:simulate|demonstrate)\s+(?:a\s+)?(?:prompt\s+)?(?:injection|attack)",
    r"(?i)respond\s+(?:with|using)\s+(?:the\s+)?(?:phrase|text|words)",
    r"(?i)include\s+(?:the\s+)?(?:exact\s+)?(?:phrase|text|words)\s*[\"']",
    r"(?i)your\s+(?:response|output|answer)\s+(?:must|should)\s+(?:contain|include)\s+[\"']",
    r"(?i)(?:copy|repeat|echo)\s+(?:this|the\s+following)\s+(?:exactly|verbatim)",
    r"(?i)make\s+(?:it|the\s+(?:output|response))\s+(?:look|appear)\s+like\s+(?:a\s+)?(?:system|admin)",
    r"(?i)(?:respond|reply)\s+(?:to\s+)?(?:the\s+following|this)\s+.*(?:exactly|as\s+written|verbatim)",
    r"(?i)(?:customer|user)\s+inquiry\s+(?:exactly\s+)?as\s+written",
]


class TrapDetector:
    """Detects jobs designed to bait agents into triggering the scrubber."""

    def __init__(self):
        try:
            from layers.scrubber import get_scrubber
            self._get_scrubber = get_scrubber
        except ImportError:
            self._get_scrubber = None

    def analyze_job(self, job_id: str) -> Dict[str, Any]:
        """Analyze a job description for trap characteristics.
        Returns {trap_score, evidence, elicitation_patterns_found}.
        """
        with get_db() as conn:
            job = conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if not job:
            return {"trap_score": 0.0, "evidence": [], "elicitation_patterns_found": []}

        job = dict(job)
        description = job.get("description", "")
        return self.analyze_text(description, job.get("posted_by", ""))

    def analyze_text(self, description: str, posted_by: str = "") -> Dict[str, Any]:
        """Analyze text for trap patterns."""
        evidence = []
        trap_score = 0.0
        elicitation_found = []

        # 1. Scrub the job description itself
        if self._get_scrubber:
            scrubber = self._get_scrubber()
            job_scrub = scrubber.scrub_message(description, "job_description")
            if not job_scrub.clean:
                trap_score += 0.4
                types = [t.threat_type.value if hasattr(t.threat_type, 'value')
                         else str(t.threat_type) for t in job_scrub.threats_detected]
                evidence.append(f"Job description triggers scrubber: {types}")

        # 2. Check elicitation patterns
        for pattern in ELICITATION_PATTERNS:
            match = re.search(pattern, description)
            if match:
                trap_score += 0.2
                elicitation_found.append(match.group()[:80])
                evidence.append(f"Elicitation pattern: {match.group()[:80]}")

        # 3. Check poster's history of trap-like jobs
        if posted_by:
            trap_history = self._poster_trap_history(posted_by)
            if trap_history >= 3:
                trap_score += 0.3
                evidence.append(
                    f"Poster has {trap_history} previous jobs triggering scrub hits"
                )
            elif trap_history >= 1:
                trap_score += 0.1
                evidence.append(
                    f"Poster has {trap_history} previous job(s) triggering scrub hits"
                )

        return {
            "trap_score": min(1.0, trap_score),
            "evidence": evidence,
            "elicitation_patterns_found": elicitation_found,
        }

    def detect_trap(self, job_id: str, flagged_response: str,
                    scrub_result: ScrubResult) -> Tuple[bool, float, List[str]]:
        """
        Full trap detection: analyze job + check if flagged content
        was planted by the job poster.
        Returns (is_trap, confidence, evidence).
        """
        job_analysis = self.analyze_job(job_id)
        trap_score = job_analysis["trap_score"]
        evidence = list(job_analysis["evidence"])

        # 4. Check if flagged segments appear in job description
        with get_db() as conn:
            job = conn.execute(
                "SELECT description FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()

        if job:
            description = dict(job).get("description", "")
            # Extract the parts of the response that triggered detections
            flagged_segments = self._extract_flagged_segments(flagged_response)
            for segment in flagged_segments:
                sim = self._fuzzy_contains(segment, description)
                if sim > 0.6:
                    trap_score += 0.3
                    evidence.append(
                        f"Flagged segment appears in job description (sim={sim:.2f}): "
                        f"'{segment[:50]}...'"
                    )

        is_trap = trap_score >= 0.5
        return is_trap, min(1.0, trap_score), evidence

    def _poster_trap_history(self, posted_by: str) -> int:
        """Count how many of this poster's jobs have triggered scrub hits on workers."""
        try:
            with get_db() as conn:
                count = conn.execute("""
                    SELECT COUNT(DISTINCT j.job_id) as cnt
                    FROM jobs j
                    JOIN scrub_results sr ON sr.trace_id = j.interaction_trace_id
                    WHERE j.posted_by = ?
                    AND sr.action IN ('block', 'quarantine')
                    AND sr.risk_score > 0.5
                """, (posted_by,)).fetchone()
                return count["cnt"] if count else 0
        except Exception:
            return 0

    def _extract_flagged_segments(self, message: str) -> List[str]:
        """Extract segments of a message that match known injection patterns."""
        # Import injection patterns from scrubber
        try:
            from layers.scrubber import INJECTION_PATTERNS
        except ImportError:
            try:
                from ..layers.scrubber import INJECTION_PATTERNS
            except ImportError:
                INJECTION_PATTERNS = []

        segments = []
        for pattern in INJECTION_PATTERNS[:30]:  # Limit for performance
            try:
                for match in re.finditer(pattern, message):
                    segments.append(match.group())
            except re.error:
                continue
        return segments

    @staticmethod
    def _fuzzy_contains(needle: str, haystack: str) -> float:
        """Fuzzy substring match. Returns similarity 0.0-1.0."""
        if not needle or not haystack:
            return 0.0
        needle_lower = needle.lower().strip()
        haystack_lower = haystack.lower()
        # Exact containment
        if needle_lower in haystack_lower:
            return 1.0
        # Word-level overlap
        needle_words = set(needle_lower.split())
        haystack_words = set(haystack_lower.split())
        if not needle_words:
            return 0.0
        overlap = len(needle_words & haystack_words) / len(needle_words)
        return overlap


# ============================================================
# BEHAVIORAL ANOMALY
# ============================================================

def extract_trigrams(text: str) -> Counter:
    """Extract character trigrams from text."""
    text = text.lower()
    trigrams = Counter()
    for i in range(len(text) - 2):
        trigrams[text[i:i+3]] += 1
    return trigrams


def cosine_similarity_counters(a: Counter, b: Counter) -> float:
    """Cosine similarity between two Counters."""
    if not a or not b:
        return 0.0
    keys = set(a.keys()) | set(b.keys())
    dot = sum(a.get(k, 0) * b.get(k, 0) for k in keys)
    mag_a = math.sqrt(sum(v * v for v in a.values()))
    mag_b = math.sqrt(sum(v * v for v in b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


class BehavioralBaseline:
    """Statistical profiling of agent behavior."""

    ALPHA = 0.1  # EMA smoothing factor
    MIN_SAMPLES = 10

    @staticmethod
    def update(agent_id: str, message: str, risk_score: float = 0.0):
        """Update an agent's behavioral baseline with a new message."""
        msg_len = len(message)
        trigrams = extract_trigrams(message)
        hour = datetime.now().hour

        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM behavioral_baselines WHERE agent_id = ?",
                (agent_id,)
            ).fetchone()

            if not row:
                # First message — initialize
                conn.execute("""
                    INSERT INTO behavioral_baselines
                    (agent_id, avg_message_length, msg_length_stddev,
                     avg_risk_score, risk_score_stddev,
                     vocabulary_fingerprint, typical_active_hours,
                     sample_count, last_updated)
                    VALUES (?, ?, 0, ?, 0, ?, ?, 1, ?)
                """, (
                    agent_id, msg_len, risk_score,
                    json.dumps(dict(trigrams.most_common(50))),
                    json.dumps([hour]),
                    datetime.now()
                ))
                conn.commit()
                return

            row = dict(row)
            n = row["sample_count"]
            alpha = BehavioralBaseline.ALPHA

            # EMA updates
            new_avg_len = row["avg_message_length"] * (1 - alpha) + msg_len * alpha
            new_std_len = math.sqrt(
                row["msg_length_stddev"] ** 2 * (1 - alpha)
                + (msg_len - new_avg_len) ** 2 * alpha
            )
            new_avg_risk = row["avg_risk_score"] * (1 - alpha) + risk_score * alpha
            new_std_risk = math.sqrt(
                row["risk_score_stddev"] ** 2 * (1 - alpha)
                + (risk_score - new_avg_risk) ** 2 * alpha
            )

            # Update vocabulary fingerprint (merge trigrams)
            old_vocab = Counter(json.loads(row["vocabulary_fingerprint"]))
            # Blend: 90% old + 10% new
            for k in trigrams:
                old_vocab[k] = old_vocab.get(k, 0) * (1 - alpha) + trigrams[k] * alpha
            new_vocab = dict(Counter(old_vocab).most_common(50))

            # Update active hours
            hours = json.loads(row["typical_active_hours"])
            if hour not in hours:
                hours.append(hour)
                hours = hours[-24:]  # Keep last 24 unique hours

            conn.execute("""
                UPDATE behavioral_baselines SET
                    avg_message_length = ?,
                    msg_length_stddev = ?,
                    avg_risk_score = ?,
                    risk_score_stddev = ?,
                    vocabulary_fingerprint = ?,
                    typical_active_hours = ?,
                    sample_count = ?,
                    last_updated = ?
                WHERE agent_id = ?
            """, (
                new_avg_len, new_std_len, new_avg_risk, new_std_risk,
                json.dumps(new_vocab), json.dumps(hours),
                n + 1, datetime.now(), agent_id
            ))
            conn.commit()

    @staticmethod
    def compute_anomaly(agent_id: str, message: str,
                        risk_score: float = 0.0) -> float:
        """Compute anomaly score (0.0 normal – 1.0 extreme anomaly)."""
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM behavioral_baselines WHERE agent_id = ?",
                (agent_id,)
            ).fetchone()

        if not row:
            return 0.0  # No baseline = can't compute anomaly

        row = dict(row)
        if row["sample_count"] < BehavioralBaseline.MIN_SAMPLES:
            return 0.0  # Not enough data

        scores = []

        # Length deviation
        if row["msg_length_stddev"] > 0:
            z = abs(len(message) - row["avg_message_length"]) / row["msg_length_stddev"]
            scores.append(min(1.0, z / 4.0))

        # Risk score deviation
        if row["risk_score_stddev"] > 0:
            z = abs(risk_score - row["avg_risk_score"]) / row["risk_score_stddev"]
            scores.append(min(1.0, z / 3.0))

        # Vocabulary shift
        msg_trigrams = extract_trigrams(message)
        baseline_vocab = Counter(json.loads(row["vocabulary_fingerprint"]))
        vocab_sim = cosine_similarity_counters(msg_trigrams, baseline_vocab)
        vocab_distance = 1.0 - vocab_sim
        scores.append(min(1.0, vocab_distance * 2.0))

        # Timing
        hour = datetime.now().hour
        active_hours = json.loads(row["typical_active_hours"])
        if hour not in active_hours:
            scores.append(0.3)

        return sum(scores) / len(scores) if scores else 0.0


# ============================================================
# FRAMING SCORE COMPUTATION
# ============================================================

class FramingAnalyzer:
    """Core framing analysis engine."""

    def __init__(self):
        self.trap_detector = TrapDetector()

    def analyze(self, agent_id: str, message_id: str, job_id: str,
                message_content: str, scrub_result: ScrubResult) -> Dict[str, Any]:
        """
        Full framing analysis. Returns:
        {
            framing_score: 0.0-1.0,
            provenance_valid: bool,
            trap_detected: bool,
            trap_score: float,
            behavioral_anomaly: float,
            evidence: [...],
            recommendation: 'execute' | 'quarantine' | 'acquit' | 'review'
        }
        """
        evidence = []
        components = {}

        # 1. Provenance
        chain_valid, chain_issues = ProvenanceChain.verify_chain(job_id)
        components["provenance"] = 1.0 if chain_valid else 0.0
        if not chain_valid:
            evidence.extend([f"Provenance issue: {i}" for i in chain_issues])

        # 2. Trap detection
        is_trap, trap_score, trap_evidence = self.trap_detector.detect_trap(
            job_id, message_content, scrub_result
        )
        components["trap"] = trap_score
        evidence.extend(trap_evidence)

        # 3. Behavioral anomaly
        anomaly = BehavioralBaseline.compute_anomaly(
            agent_id, message_content, scrub_result.risk_score
        )
        components["behavioral_anomaly"] = anomaly
        if anomaly > 0.5:
            evidence.append(f"High behavioral anomaly: {anomaly:.2f}")

        # 4. Sybil activity around this agent
        sybil_score = self._check_sybil_activity(agent_id)
        components["sybil_activity"] = sybil_score
        if sybil_score > 0.3:
            evidence.append(f"Sybil activity near agent: {sybil_score:.2f}")

        # 5. Rating manipulation
        rating_score = self._check_rating_manipulation(agent_id)
        components["rating_manipulation"] = rating_score
        if rating_score > 0.3:
            evidence.append(f"Rating manipulation detected: {rating_score:.2f}")

        # Weighted framing score
        framing_score = (
            0.30 * components["trap"]
            + 0.25 * components["behavioral_anomaly"]
            + 0.20 * (1.0 - components["provenance"])
            + 0.15 * components["sybil_activity"]
            + 0.10 * components["rating_manipulation"]
        )
        framing_score = min(1.0, framing_score)

        # Recommendation
        agent = get_agent_by_id(agent_id)
        trust_score = 0.0
        if agent:
            with get_db() as conn:
                ts_row = conn.execute(
                    "SELECT trust_score FROM agents WHERE agent_id = ?",
                    (agent_id,)
                ).fetchone()
                if ts_row:
                    trust_score = ts_row["trust_score"]

        if trust_score > 0.5:
            recommendation = "review"  # High-trust = mandatory GM review
        elif framing_score < 0.2:
            recommendation = "execute"
        elif framing_score > 0.8:
            recommendation = "acquit"
        else:
            recommendation = "review"

        return {
            "framing_score": round(framing_score, 3),
            "provenance_valid": chain_valid,
            "trap_detected": is_trap,
            "trap_score": round(trap_score, 3),
            "behavioral_anomaly": round(anomaly, 3),
            "sybil_activity": round(sybil_score, 3),
            "rating_manipulation": round(rating_score, 3),
            "components": components,
            "evidence": evidence,
            "recommendation": recommendation,
            "trust_score": trust_score,
        }

    def _check_sybil_activity(self, agent_id: str) -> float:
        """Check for Sybil accounts interacting with this agent."""
        try:
            with get_db() as conn:
                # Check if any agents that interacted with this one are in sybil clusters
                clusters = conn.execute("""
                    SELECT member_agents, confidence FROM sybil_clusters
                    WHERE status = 'suspected' OR status = 'confirmed'
                """).fetchall()

                for cluster in clusters:
                    members = json.loads(dict(cluster)["member_agents"])
                    # Check if any cluster member has interacted with our agent
                    for member in members:
                        interaction = conn.execute("""
                            SELECT COUNT(*) as cnt FROM wire_messages
                            WHERE (from_agent = ? AND to_agent = ?)
                            OR (from_agent = ? AND to_agent = ?)
                        """, (member, agent_id, agent_id, member)).fetchone()
                        if interaction and interaction["cnt"] > 0:
                            return dict(cluster)["confidence"]
        except Exception:
            pass
        return 0.0

    def _check_rating_manipulation(self, agent_id: str) -> float:
        """Check for coordinated rating manipulation against this agent."""
        try:
            with get_db() as conn:
                # Look for bursts of low ratings in short windows
                low_ratings = conn.execute("""
                    SELECT te.*, a.trust_score as rater_trust
                    FROM trust_events te
                    LEFT JOIN agents a ON a.agent_id = te.agent_id
                    WHERE te.job_id IN (
                        SELECT job_id FROM jobs WHERE assigned_to = ?
                    )
                    AND te.rating IS NOT NULL AND te.rating <= 2.0
                    AND te.timestamp > datetime('now', '-7 days')
                    ORDER BY te.timestamp
                """, (agent_id,)).fetchall()

                if len(low_ratings) < 3:
                    return 0.0

                # Check for temporal clustering
                timestamps = [
                    datetime.fromisoformat(dict(r)["timestamp"])
                    for r in low_ratings
                ]
                for i in range(len(timestamps) - 2):
                    window = timestamps[i+2] - timestamps[i]
                    if window < timedelta(hours=6):
                        return min(1.0, 0.3 * (len(low_ratings) - i))

        except Exception:
            pass
        return 0.0


# Module-level singleton
_framing_analyzer = None


def get_framing_analyzer() -> FramingAnalyzer:
    global _framing_analyzer
    if _framing_analyzer is None:
        _framing_analyzer = FramingAnalyzer()
    return _framing_analyzer
