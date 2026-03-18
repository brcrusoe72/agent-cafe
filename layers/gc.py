"""
Agent Café — Garbage Collection
Cleans up expired data, stale records, and dead weight.

Runs periodically (via Grandmaster heartbeat or explicit trigger).
Never deletes evidence (corpses, immune events, death registry).
Only removes operational cruft.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

try:
    from ..db import get_db
except ImportError:
    from db import get_db


class GarbageCollector:
    """
    Cleans up the database.
    
    What gets cleaned:
    - Expired jobs (open + past expires_at) → marked expired
    - Old trace events (> 30 days, job completed) → deleted
    - Stale scrub results (> 30 days) → deleted
    - Old wire messages (> 90 days, job completed) → deleted
    - Delivered federation broadcasts (> 7 days) → deleted
    - Stale remote jobs (> 7 days past expiry) → deleted
    - Old cafe_events (> 30 days, processed) → deleted
    - Rejected/withdrawn bids on completed jobs (> 14 days) → deleted
    
    What NEVER gets cleaned:
    - Agent corpses (permanent record)
    - Immune events (audit trail)
    - Global death registry (permanent)
    - Trust events (reputation is forever)
    - Active/open job data
    - Model versions (training history)
    """
    
    def __init__(
        self,
        trace_max_days: int = 30,
        scrub_max_days: int = 30,
        wire_max_days: int = 90,
        event_max_days: int = 30,
        broadcast_max_days: int = 7,
        remote_job_max_days: int = 7,
        bid_max_days: int = 14,
        pack_action_max_days: int = 14,
        grandmaster_log_max_days: int = 30,
        scrub_log_max_days: int = 30,
        payment_event_max_days: int = 90,
    ):
        self.trace_max_days = trace_max_days
        self.scrub_max_days = scrub_max_days
        self.wire_max_days = wire_max_days
        self.event_max_days = event_max_days
        self.broadcast_max_days = broadcast_max_days
        self.remote_job_max_days = remote_job_max_days
        self.bid_max_days = bid_max_days
        self.pack_action_max_days = pack_action_max_days
        self.grandmaster_log_max_days = grandmaster_log_max_days
        self.scrub_log_max_days = scrub_log_max_days
        self.payment_event_max_days = payment_event_max_days
    
    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Run full garbage collection cycle.
        
        Args:
            dry_run: If True, count what would be deleted but don't delete.
            
        Returns dict of {category: count_deleted}
        """
        results = {}
        
        results["expired_jobs"] = self._expire_jobs(dry_run)
        results["old_trace_events"] = self._clean_trace_events(dry_run)
        results["old_scrub_results"] = self._clean_scrub_results(dry_run)
        results["old_wire_messages"] = self._clean_wire_messages(dry_run)
        results["old_cafe_events"] = self._clean_cafe_events(dry_run)
        results["stale_bids"] = self._clean_stale_bids(dry_run)
        results["stale_remote_jobs"] = self._clean_remote_jobs(dry_run)
        results["delivered_broadcasts"] = self._clean_broadcasts(dry_run)
        results["old_pack_actions"] = self._clean_pack_actions(dry_run)
        results["old_grandmaster_log"] = self._clean_grandmaster_log(dry_run)
        results["old_scrub_logs"] = self._clean_scrub_logs(dry_run)
        results["old_payment_events"] = self._clean_payment_events(dry_run)
        results["db_vacuum"] = self._vacuum(dry_run)
        
        results["total_cleaned"] = sum(
            v for k, v in results.items() 
            if isinstance(v, int) and k != "db_vacuum"
        )
        results["dry_run"] = dry_run
        results["timestamp"] = datetime.now(timezone.utc).isoformat()
        
        return results
    
    def _expire_jobs(self, dry_run: bool) -> int:
        """Mark open jobs past their expiry as expired."""
        now = datetime.now().isoformat()
        with get_db() as conn:
            if dry_run:
                count = conn.execute("""
                    SELECT COUNT(*) FROM jobs
                    WHERE status = 'open' AND expires_at IS NOT NULL AND expires_at < ?
                """, (now,)).fetchone()[0]
                return count
            
            cursor = conn.execute("""
                UPDATE jobs SET status = 'expired'
                WHERE status = 'open' AND expires_at IS NOT NULL AND expires_at < ?
            """, (now,))
            conn.commit()
            return cursor.rowcount
    
    def _clean_trace_events(self, dry_run: bool) -> int:
        """Delete old trace events from completed/cancelled jobs."""
        cutoff = (datetime.now() - timedelta(days=self.trace_max_days)).isoformat()
        with get_db() as conn:
            # Only delete trace events for completed/cancelled/expired jobs
            if dry_run:
                count = conn.execute("""
                    SELECT COUNT(*) FROM trace_events te
                    JOIN interaction_traces it ON te.trace_id = it.trace_id
                    JOIN jobs j ON it.job_id = j.job_id
                    WHERE te.timestamp < ?
                    AND j.status IN ('completed', 'cancelled', 'expired', 'killed')
                """, (cutoff,)).fetchone()[0]
                return count
            
            cursor = conn.execute("""
                DELETE FROM trace_events WHERE trace_id IN (
                    SELECT te.trace_id FROM trace_events te
                    JOIN interaction_traces it ON te.trace_id = it.trace_id
                    JOIN jobs j ON it.job_id = j.job_id
                    WHERE te.timestamp < ?
                    AND j.status IN ('completed', 'cancelled', 'expired', 'killed')
                )
            """, (cutoff,))
            conn.commit()
            return cursor.rowcount
    
    def _clean_scrub_results(self, dry_run: bool) -> int:
        """Delete old scrub results (pass/clean only — blocks are evidence)."""
        cutoff = (datetime.now() - timedelta(days=self.scrub_max_days)).isoformat()
        with get_db() as conn:
            if dry_run:
                count = conn.execute("""
                    SELECT COUNT(*) FROM scrub_results
                    WHERE timestamp < ? AND action IN ('pass', 'clean')
                """, (cutoff,)).fetchone()[0]
                return count
            
            cursor = conn.execute("""
                DELETE FROM scrub_results
                WHERE timestamp < ? AND action IN ('pass', 'clean')
            """, (cutoff,))
            conn.commit()
            return cursor.rowcount
    
    def _clean_wire_messages(self, dry_run: bool) -> int:
        """Delete old wire messages from completed jobs."""
        cutoff = (datetime.now() - timedelta(days=self.wire_max_days)).isoformat()
        with get_db() as conn:
            if dry_run:
                count = conn.execute("""
                    SELECT COUNT(*) FROM wire_messages wm
                    JOIN jobs j ON wm.job_id = j.job_id
                    WHERE wm.timestamp < ?
                    AND j.status IN ('completed', 'cancelled', 'expired')
                """, (cutoff,)).fetchone()[0]
                return count
            
            cursor = conn.execute("""
                DELETE FROM wire_messages WHERE job_id IN (
                    SELECT wm.job_id FROM wire_messages wm
                    JOIN jobs j ON wm.job_id = j.job_id
                    WHERE wm.timestamp < ?
                    AND j.status IN ('completed', 'cancelled', 'expired')
                )
            """, (cutoff,))
            conn.commit()
            return cursor.rowcount
    
    def _clean_cafe_events(self, dry_run: bool) -> int:
        """Delete old processed cafe events."""
        cutoff = (datetime.now() - timedelta(days=self.event_max_days)).isoformat()
        with get_db() as conn:
            if dry_run:
                count = conn.execute("""
                    SELECT COUNT(*) FROM cafe_events
                    WHERE timestamp < ? AND processed = 1
                """, (cutoff,)).fetchone()[0]
                return count
            
            cursor = conn.execute("""
                DELETE FROM cafe_events
                WHERE timestamp < ? AND processed = 1
            """, (cutoff,))
            conn.commit()
            return cursor.rowcount
    
    def _clean_stale_bids(self, dry_run: bool) -> int:
        """Delete rejected/withdrawn bids on completed jobs."""
        cutoff = (datetime.now() - timedelta(days=self.bid_max_days)).isoformat()
        with get_db() as conn:
            if dry_run:
                count = conn.execute("""
                    SELECT COUNT(*) FROM bids b
                    JOIN jobs j ON b.job_id = j.job_id
                    WHERE b.status IN ('rejected', 'withdrawn')
                    AND b.submitted_at < ?
                    AND j.status IN ('completed', 'cancelled', 'expired')
                """, (cutoff,)).fetchone()[0]
                return count
            
            cursor = conn.execute("""
                DELETE FROM bids WHERE bid_id IN (
                    SELECT b.bid_id FROM bids b
                    JOIN jobs j ON b.job_id = j.job_id
                    WHERE b.status IN ('rejected', 'withdrawn')
                    AND b.submitted_at < ?
                    AND j.status IN ('completed', 'cancelled', 'expired')
                )
            """, (cutoff,))
            conn.commit()
            return cursor.rowcount
    
    def _clean_remote_jobs(self, dry_run: bool) -> int:
        """Delete stale remote job listings."""
        cutoff = (datetime.now() - timedelta(days=self.remote_job_max_days)).isoformat()
        with get_db() as conn:
            try:
                if dry_run:
                    count = conn.execute("""
                        SELECT COUNT(*) FROM remote_jobs
                        WHERE (expires_at IS NOT NULL AND expires_at < ?)
                        OR (received_at < ? AND status != 'open')
                    """, (cutoff, cutoff)).fetchone()[0]
                    return count
                
                cursor = conn.execute("""
                    DELETE FROM remote_jobs
                    WHERE (expires_at IS NOT NULL AND expires_at < ?)
                    OR (received_at < ? AND status != 'open')
                """, (cutoff, cutoff))
                conn.commit()
                return cursor.rowcount
            except Exception:
                return 0  # Table might not exist if federation never initialized
    
    def _clean_broadcasts(self, dry_run: bool) -> int:
        """Delete old delivered federation broadcasts."""
        cutoff = (datetime.now() - timedelta(days=self.broadcast_max_days)).isoformat()
        with get_db() as conn:
            try:
                if dry_run:
                    count = conn.execute("""
                        SELECT COUNT(*) FROM pending_broadcasts
                        WHERE delivered = 1 AND delivered_at < ?
                    """, (cutoff,)).fetchone()[0]
                    return count
                
                cursor = conn.execute("""
                    DELETE FROM pending_broadcasts
                    WHERE delivered = 1 AND delivered_at < ?
                """, (cutoff,))
                conn.commit()
                return cursor.rowcount
            except Exception:
                return 0
    
    def _clean_pack_actions(self, dry_run: bool) -> int:
        """Delete old pack patrol actions."""
        cutoff = (datetime.now() - timedelta(days=self.pack_action_max_days)).isoformat()
        with get_db() as conn:
            try:
                if dry_run:
                    return conn.execute(
                        "SELECT COUNT(*) FROM pack_actions WHERE timestamp < ?",
                        (cutoff,)
                    ).fetchone()[0]
                cursor = conn.execute(
                    "DELETE FROM pack_actions WHERE timestamp < ?", (cutoff,)
                )
                conn.commit()
                return cursor.rowcount
            except Exception:
                return 0
    
    def _clean_grandmaster_log(self, dry_run: bool) -> int:
        """Delete old grandmaster log/decision entries."""
        cutoff = (datetime.now() - timedelta(days=self.grandmaster_log_max_days)).isoformat()
        with get_db() as conn:
            total = 0
            for table in ("grandmaster_log", "grandmaster_decisions"):
                try:
                    if dry_run:
                        total += conn.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE timestamp < ?",
                            (cutoff,)
                        ).fetchone()[0]
                    else:
                        cursor = conn.execute(
                            f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,)
                        )
                        total += cursor.rowcount
                except Exception:
                    pass
            if not dry_run:
                conn.commit()
            return total
    
    def _clean_scrub_logs(self, dry_run: bool) -> int:
        """Delete old middleware scrub logs and verdicts."""
        cutoff = (datetime.now() - timedelta(days=self.scrub_log_max_days)).isoformat()
        with get_db() as conn:
            total = 0
            for table in ("middleware_scrub_log", "scrubber_verdicts"):
                try:
                    if dry_run:
                        total += conn.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE timestamp < ?",
                            (cutoff,)
                        ).fetchone()[0]
                    else:
                        cursor = conn.execute(
                            f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,)
                        )
                        total += cursor.rowcount
                except Exception:
                    pass
            if not dry_run:
                conn.commit()
            return total
    
    def _clean_payment_events(self, dry_run: bool) -> int:
        """Delete old completed payment events."""
        cutoff = (datetime.now() - timedelta(days=self.payment_event_max_days)).isoformat()
        with get_db() as conn:
            try:
                if dry_run:
                    return conn.execute(
                        "SELECT COUNT(*) FROM payment_events WHERE timestamp < ?",
                        (cutoff,)
                    ).fetchone()[0]
                cursor = conn.execute(
                    "DELETE FROM payment_events WHERE timestamp < ?", (cutoff,)
                )
                conn.commit()
                return cursor.rowcount
            except Exception:
                return 0
    
    def _vacuum(self, dry_run: bool) -> int:
        """Run SQLite VACUUM to reclaim space. Returns 1 if run, 0 if skipped."""
        if dry_run:
            return 0
        try:
            # VACUUM can't run inside a transaction, need a raw connection
            import sqlite3
            from db import DATABASE_PATH
            conn = sqlite3.connect(DATABASE_PATH)
            conn.execute("VACUUM")
            conn.close()
            return 1
        except Exception:
            return 0
    
    def db_size_bytes(self) -> int:
        """Get current database file size."""
        try:
            from db import DATABASE_PATH
            return DATABASE_PATH.stat().st_size
        except Exception:
            return 0
    
    def table_sizes(self) -> Dict[str, int]:
        """Get row counts for all tables."""
        tables = [
            "agents", "jobs", "bids", "wire_messages", "interaction_traces",
            "trace_events", "scrub_results", "trust_events", "immune_events",
            "agent_corpses", "wallets", "capability_challenges", "treasury",
            "known_patterns", "cafe_events", "grandmaster_log",
            "grandmaster_decisions", "pack_actions", "pack_evaluations",
            "middleware_scrub_log", "scrubber_verdicts", "payment_events",
            "interaction_log", "canary_log",
        ]
        # Federation tables (may not exist)
        fed_tables = [
            "global_deaths", "remote_jobs", "remote_trust_cache",
            "known_peers", "federated_samples", "model_versions",
            "pending_broadcasts", "federation_reputation",
        ]
        
        sizes = {}
        with get_db() as conn:
            for table in tables + fed_tables:
                try:
                    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    sizes[table] = count
                except Exception:
                    pass  # Table doesn't exist
        
        return sizes


# Global singleton
gc = GarbageCollector()
