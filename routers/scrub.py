"""
Agent Café - Scrubbing Router
Endpoints for scrubber statistics and pattern management.
Operator-only access for security insights and pattern learning.
"""

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import json
import uuid
from fastapi import APIRouter, HTTPException, Depends, Request, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cafe_logging import get_logger
logger = get_logger(__name__)

try:
    from ..middleware.auth import get_operator_access, scrub_daily_limiter
    from ..middleware.scrub_middleware import get_scrub_stats, get_recent_threats
    from ..layers.scrubber import get_scrubber, learn_from_agent_kill, get_scrubber_stats
    from ..models import ThreatType, ThreatDetection, ScrubResult
    from ..db import get_known_patterns, add_known_pattern, get_db
except ImportError:
    from middleware.auth import get_operator_access, scrub_daily_limiter
    from middleware.scrub_middleware import get_scrub_stats, get_recent_threats
    from layers.scrubber import get_scrubber, learn_from_agent_kill, get_scrubber_stats
    from models import ThreatType, ThreatDetection, ScrubResult
    from db import get_known_patterns, add_known_pattern, get_db

import json
import uuid

router = APIRouter()


# === REQUEST/RESPONSE MODELS ===

class PatternAddRequest(BaseModel):
    threat_type: str
    pattern_regex: str
    description: str
    confidence_weight: Optional[float] = 1.0


class TestScrubRequest(BaseModel):
    message: str
    message_type: Optional[str] = "general"
    job_context: Optional[Dict[str, Any]] = None


class LearnFromKillRequest(BaseModel):
    agent_id: str
    evidence_messages: List[str]
    attack_patterns: List[str]


class ScrubStatsResponse(BaseModel):
    total_processed: int
    actions: Dict[str, int]  # action -> count
    threats_by_type: Dict[str, int]
    quarantines_24h: int
    blocks_24h: int
    recent_patterns_learned: int
    scrubber_engine_stats: Dict[str, Any]


class ThreatAnalysisResponse(BaseModel):
    recent_threats: List[Dict[str, Any]]
    threat_trends: Dict[str, List[Dict[str, Any]]]
    top_threat_types: List[Dict[str, Any]]
    attack_sophistication_score: float


class PatternAnalysisResponse(BaseModel):
    total_patterns: int
    patterns_by_type: Dict[str, int]
    recent_additions: List[Dict[str, Any]]
    effectiveness_scores: Dict[str, float]
    recommended_new_patterns: List[str]


# === ENDPOINTS ===


class AnalyzeRequest(BaseModel):
    message: str
    message_type: Optional[str] = "general"


@router.post("/analyze")
async def analyze_message(req: AnalyzeRequest, request: Request):
    """
    Analyze a message for threats. Free public endpoint.
    - Unauthenticated: 100 requests/day per IP, limited response (verdict only)
    - Registered agents (valid API key): unlimited, full response with pattern details
    """
    if not req.message or not req.message.strip():
        return JSONResponse(status_code=400, content={"error": "Message cannot be empty"})
    
    # Determine if caller is authenticated
    is_authenticated = False
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            try:
                from ..db import get_agent_by_api_key
            except ImportError:
                from db import get_agent_by_api_key
            agent = get_agent_by_api_key(token)
            if agent:
                is_authenticated = True
        except Exception:
            pass

    # Rate limit unauthenticated users
    if not is_authenticated:
        client_ip = request.client.host if request.client else "unknown"
        if not scrub_daily_limiter.is_allowed(f"scrub:{client_ip}", max_per_day=100):
            raise HTTPException(
                status_code=429,
                detail="Daily limit reached (100 requests/day). Register an agent for unlimited access."
            )

    try:
        scrubber = get_scrubber()
        result = scrubber.scrub_message(
            message=req.message,
            message_type=req.message_type or "general",
        )

        # Base response for everyone
        response = {
            "clean": result.clean,
            "action": result.action,
            "risk_score": result.risk_score,
            "threat_types": [t.threat_type.value if hasattr(t.threat_type, 'value') else str(t.threat_type) for t in result.threats_detected],
        }

        # Full details only for authenticated agents
        if is_authenticated:
            response["threats_detected"] = [
                {
                    "threat_type": t.threat_type.value if hasattr(t.threat_type, 'value') else str(t.threat_type),
                    "confidence": t.confidence,
                    "evidence": t.evidence,
                    "location": t.location,
                }
                for t in result.threats_detected
            ]
            response["scrubbed_message"] = result.scrubbed_message

        return response

    except Exception as e:
        logger.warning("Unhandled error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@router.get("/stats", response_model=ScrubStatsResponse)
async def get_scrubbing_statistics(
    _: bool = Depends(get_operator_access)
) -> ScrubStatsResponse:
    """
    Get comprehensive scrubbing statistics.
    Operator-only endpoint for security monitoring.
    """
    try:
        # Get basic scrub stats from middleware
        basic_stats = get_scrub_stats()
        
        # Get additional statistics from database
        with get_db() as conn:
            # Blocks in last 24 hours
            blocks_24h = conn.execute("""
                SELECT COUNT(*) as count
                FROM scrub_results 
                WHERE action = 'block' 
                AND timestamp > datetime('now', '-24 hours')
            """).fetchone()['count']
            
            # Recent patterns learned (last 30 days)
            recent_patterns = conn.execute("""
                SELECT COUNT(*) as count
                FROM known_patterns 
                WHERE created_at > datetime('now', '-30 days')
                AND learned_from_agent IS NOT NULL
            """).fetchone()['count']
        
        return ScrubStatsResponse(
            total_processed=basic_stats.get("total_messages_processed", 0),
            actions=basic_stats.get("actions", {}),
            threats_by_type=basic_stats.get("threats_detected", {}),
            quarantines_24h=basic_stats.get("quarantines_24h", 0),
            blocks_24h=blocks_24h,
            recent_patterns_learned=recent_patterns,
            scrubber_engine_stats=basic_stats.get("scrubber_stats", {})
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get scrub statistics: {str(e)}"
        )


@router.get("/threats/analysis", response_model=ThreatAnalysisResponse)
async def analyze_threat_patterns(
    hours: int = Query(24, description="Hours to analyze", ge=1, le=720),
    _: bool = Depends(get_operator_access)
) -> ThreatAnalysisResponse:
    """
    Deep analysis of threat patterns and trends.
    Helps operator understand attack evolution and system effectiveness.
    """
    try:
        # Get recent threats
        recent_threats = get_recent_threats(limit=200)
        
        # Calculate time window
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        # Filter threats by time window
        time_filtered_threats = [
            threat for threat in recent_threats 
            if datetime.fromisoformat(threat['timestamp'].replace('Z', '+00:00')).replace(tzinfo=None) > cutoff_time
        ]
        
        # Analyze threat trends by hour
        threat_trends = {}
        for threat in time_filtered_threats:
            timestamp = datetime.fromisoformat(threat['timestamp'].replace('Z', '+00:00')).replace(tzinfo=None)
            hour = timestamp.replace(minute=0, second=0, microsecond=0)
            hour_key = hour.isoformat()
            
            if hour_key not in threat_trends:
                threat_trends[hour_key] = []
            threat_trends[hour_key].append(threat)
        
        # Count threat types
        threat_type_counts = {}
        total_confidence = 0.0
        threat_count = 0
        
        for threat in time_filtered_threats:
            for threat_detail in threat['threats']:
                threat_type = threat_detail['threat_type']
                threat_type_counts[threat_type] = threat_type_counts.get(threat_type, 0) + 1
                total_confidence += threat_detail['confidence']
                threat_count += 1
        
        # Sort by frequency
        top_threat_types = [
            {"type": threat_type, "count": count, "percentage": (count / max(1, len(time_filtered_threats))) * 100}
            for threat_type, count in sorted(threat_type_counts.items(), key=lambda x: x[1], reverse=True)
        ]
        
        # Calculate attack sophistication score (0-1)
        # Based on: diversity of attack types, confidence levels, encoding usage
        unique_types = len(threat_type_counts)
        avg_confidence = total_confidence / max(1, threat_count)
        encoding_attacks = threat_type_counts.get('payload_smuggling', 0) + threat_type_counts.get('recursive_injection', 0)
        
        sophistication_score = min(1.0, (
            (unique_types / 9.0) * 0.4 +  # Type diversity (max 9 threat types)
            avg_confidence * 0.4 +         # Average confidence
            (encoding_attacks / max(1, len(time_filtered_threats))) * 0.2  # Encoding sophistication
        ))
        
        return ThreatAnalysisResponse(
            recent_threats=time_filtered_threats[:50],  # Limit response size
            threat_trends={k: v[:10] for k, v in threat_trends.items()},  # Limit per hour
            top_threat_types=top_threat_types[:10],
            attack_sophistication_score=round(sophistication_score, 3)
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to analyze threat patterns: {str(e)}"
        )


@router.get("/patterns", response_model=PatternAnalysisResponse)
async def analyze_detection_patterns(
    threat_type: Optional[str] = Query(None, description="Filter by threat type"),
    _: bool = Depends(get_operator_access)
) -> PatternAnalysisResponse:
    """
    Analyze detection patterns and their effectiveness.
    Shows pattern evolution and suggests improvements.
    """
    try:
        # Get known patterns
        filter_type = ThreatType(threat_type) if threat_type else None
        patterns = get_known_patterns(filter_type)
        
        # Count by type
        patterns_by_type = {}
        recent_additions = []
        now = datetime.now()
        
        for pattern in patterns:
            threat_type = pattern['threat_type']
            patterns_by_type[threat_type] = patterns_by_type.get(threat_type, 0) + 1
            
            # Check if recent (last 30 days)
            created_at = datetime.fromisoformat(pattern['created_at'])
            if (now - created_at).days <= 30:
                recent_additions.append({
                    "pattern_id": pattern['pattern_id'],
                    "threat_type": pattern['threat_type'],
                    "description": pattern['description'],
                    "created_at": pattern['created_at'],
                    "learned_from_agent": pattern.get('learned_from_agent')
                })
        
        # Calculate effectiveness scores by analyzing recent scrub results
        effectiveness_scores = {}
        with get_db() as conn:
            # For each threat type, calculate how often it leads to successful detection
            for threat_type in ThreatType:
                # Count detections vs. total messages for this threat type
                detection_count = conn.execute("""
                    SELECT COUNT(*) as count
                    FROM scrub_results 
                    WHERE threats_detected LIKE ? 
                    AND timestamp > datetime('now', '-7 days')
                """, (f'%{threat_type.value}%',)).fetchone()['count']
                
                total_count = max(detection_count, 1)  # Avoid division by zero
                
                # Simple effectiveness score (could be more sophisticated)
                effectiveness_scores[threat_type.value] = min(1.0, detection_count / 10.0)
        
        # Generate recommended new patterns based on recent undetected issues
        recommended_patterns = await _generate_pattern_recommendations()
        
        return PatternAnalysisResponse(
            total_patterns=len(patterns),
            patterns_by_type=patterns_by_type,
            recent_additions=sorted(recent_additions, key=lambda x: x['created_at'], reverse=True)[:20],
            effectiveness_scores=effectiveness_scores,
            recommended_new_patterns=recommended_patterns
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to analyze patterns: {str(e)}"
        )


@router.post("/patterns")
async def add_detection_pattern(
    pattern_request: PatternAddRequest,
    _: bool = Depends(get_operator_access)
):
    """
    Add a new threat detection pattern to the scrubber.
    Operator can manually add patterns based on observed attacks.
    """
    try:
        # Validate threat type
        try:
            threat_type = ThreatType(pattern_request.threat_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid threat type: {pattern_request.threat_type}. Valid types: {[t.value for t in ThreatType]}"
            )
        
        # Validate regex pattern
        import re
        try:
            re.compile(pattern_request.pattern_regex)
        except re.error as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid regex pattern: {str(e)}"
            )
        
        # Add pattern to database
        pattern_id = add_known_pattern(
            threat_type=threat_type,
            pattern_regex=pattern_request.pattern_regex,
            description=pattern_request.description,
            learned_from_agent=None  # Manually added by operator
        )
        
        # Update scrubber cache
        scrubber = get_scrubber()
        if threat_type not in scrubber.known_patterns:
            scrubber.known_patterns[threat_type] = []
        scrubber.known_patterns[threat_type].append(pattern_request.pattern_regex)
        
        return {
            "pattern_id": pattern_id,
            "threat_type": threat_type.value,
            "status": "added",
            "message": "Pattern successfully added to scrubber"
        }
    
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=500,
            detail=f"Failed to add pattern: {str(e)}"
        )


@router.post("/test")
async def test_scrubber(
    test_request: TestScrubRequest,
    _: bool = Depends(get_operator_access)
) -> ScrubResult:
    """
    Test the scrubber against a message without actually processing it.
    Useful for testing new patterns and analyzing attack attempts.
    """
    try:
        scrubber = get_scrubber()
        result = scrubber.scrub_message(
            message=test_request.message,
            message_type=test_request.message_type or "general",
            job_context=test_request.job_context
        )
        
        return result
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to test scrubber: {str(e)}"
        )


@router.post("/learn")
async def learn_from_agent_kill(
    learn_request: LearnFromKillRequest,
    _: bool = Depends(get_operator_access)
):
    """
    Teach the scrubber new patterns from a killed agent's attack attempts.
    Called by immune system when an agent is terminated for security violations.
    """
    try:
        # Learn patterns from the kill
        learn_from_agent_kill(
            agent_id=learn_request.agent_id,
            evidence_messages=learn_request.evidence_messages,
            attack_patterns=learn_request.attack_patterns
        )
        
        return {
            "agent_id": learn_request.agent_id,
            "patterns_learned": len(learn_request.attack_patterns),
            "status": "learned",
            "message": f"Scrubber learned {len(learn_request.attack_patterns)} new patterns from agent kill"
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to learn from kill: {str(e)}"
        )


@router.get("/health")
async def scrubber_health_check():
    """
    Health check for scrubber system.
    Public endpoint to verify scrubber is operational.
    """
    try:
        scrubber = get_scrubber()
        stats = get_scrubber_stats()
        
        # Test scrubber with a safe message
        test_result = scrubber.scrub_message("Hello, this is a test message.", "general")
        
        return {
            "status": "healthy",
            "scrubber_loaded": True,
            "total_patterns": stats.get("total_known_patterns", 0),
            "test_result": "pass" if test_result.action == "pass" else "unexpected",
            "last_check": datetime.now().isoformat()
        }
    
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "last_check": datetime.now().isoformat()
        }


# === HELPER FUNCTIONS ===

async def _generate_pattern_recommendations() -> List[str]:
    """
    Generate recommended new patterns based on analysis of recent near-misses.
    This analyzes messages that had low-medium risk scores but weren't caught.
    """
    try:
        recommendations = []
        
        with get_db() as conn:
            # Look for messages with moderate risk scores that passed
            moderate_risk_messages = conn.execute("""
                SELECT original_message, risk_score, threats_detected
                FROM scrub_results 
                WHERE risk_score BETWEEN 0.15 AND 0.35
                AND action = 'pass'
                AND timestamp > datetime('now', '-7 days')
                ORDER BY risk_score DESC
                LIMIT 50
            """).fetchall()
            
            # Analyze common patterns in these messages
            suspicious_phrases = {}
            
            for row in moderate_risk_messages:
                message = row['original_message'].lower()
                
                # Look for repeated suspicious phrases
                import re
                words = re.findall(r'\w+', message)
                
                for i in range(len(words) - 2):
                    phrase = ' '.join(words[i:i+3])  # 3-word phrases
                    if any(keyword in phrase for keyword in ['system', 'ignore', 'override', 'admin', 'key', 'token']):
                        suspicious_phrases[phrase] = suspicious_phrases.get(phrase, 0) + 1
            
            # Recommend patterns for phrases that appear frequently
            for phrase, count in suspicious_phrases.items():
                if count >= 3:  # Appears in at least 3 different messages
                    pattern = r'(?i)' + re.escape(phrase).replace(r'\ ', r'\s+')
                    recommendations.append(pattern)
        
        return recommendations[:5]  # Return top 5 recommendations
    
    except Exception as e:
        logger.error("Error generating pattern recommendations: %s", e)
        return []


# === PERIODIC MAINTENANCE ===

async def cleanup_old_scrub_results(days_to_keep: int = 90):
    """
    Clean up old scrub results to prevent database bloat.
    Keep detailed logs for recent period, summarize older data.
    """
    try:
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        
        with get_db() as conn:
            # Count records to be deleted
            count = conn.execute("""
                SELECT COUNT(*) as count
                FROM scrub_results 
                WHERE timestamp < ? 
                AND action IN ('pass', 'clean')
            """, (cutoff_date.isoformat(),)).fetchone()['count']
            
            # Delete old low-risk records (keep threats for analysis)
            conn.execute("""
                DELETE FROM scrub_results 
                WHERE timestamp < ? 
                AND action IN ('pass', 'clean')
                AND risk_score < 0.1
            """, (cutoff_date.isoformat(),))
            
            conn.commit()
            
            return {
                "cleaned_records": count,
                "cutoff_date": cutoff_date.isoformat(),
                "status": "completed"
            }
    
    except Exception as e:
        return {
            "error": str(e),
            "status": "failed"
        }