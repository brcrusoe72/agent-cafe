"""
Agent Café - Grandmaster Challenger
Capability challenge generation and verification.
Synthetic tests to prove claimed capabilities.
"""

import json
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum

try:
    from ..models import CapabilityChallenge, Agent
    from ..db import get_db, get_agent_by_id
except ImportError:
    from models import CapabilityChallenge, Agent
    from db import get_db, get_agent_by_id


class ChallengeType(str, Enum):
    CODE_GENERATION = "code_generation"
    DATA_ANALYSIS = "data_analysis"
    RESEARCH = "research"
    WRITING = "writing"
    CALCULATION = "calculation"
    CLASSIFICATION = "classification"
    TRANSLATION = "translation"
    API_USAGE = "api_usage"
    WORKFLOW = "workflow"
    PROBLEM_SOLVING = "problem_solving"


@dataclass
class ChallengeTemplate:
    """Template for generating capability challenges."""
    capability: str
    challenge_type: ChallengeType
    template: str
    expected_response_pattern: str
    scoring_criteria: List[str]
    time_limit_minutes: int
    difficulty_level: str  # basic|intermediate|advanced


class CapabilityChallenger:
    """Generates and validates capability challenges to verify agent claims."""
    
    def __init__(self):
        self.challenge_templates = self._load_challenge_templates()
        self.verification_rules = self._load_verification_rules()
    
    def generate_challenge(self, agent_id: str, capability: str) -> str:
        """Generate a challenge for a specific capability."""
        # Get agent context
        agent = get_agent_by_id(agent_id)
        if not agent:
            raise ValueError("Agent not found")
        
        # Select appropriate challenge template
        template = self._select_challenge_template(capability, agent)
        if not template:
            raise ValueError(f"No challenge template for capability: {capability}")
        
        # Generate specific challenge data
        challenge_data = self._generate_challenge_data(template, agent)
        
        # Create challenge record
        challenge_id = f"challenge_{uuid.uuid4().hex[:16]}"
        expires_at = datetime.now() + timedelta(minutes=template.time_limit_minutes)
        
        with get_db() as conn:
            conn.execute("""
                INSERT INTO capability_challenges (
                    challenge_id, agent_id, capability, challenge_data,
                    expected_response_schema, generated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                challenge_id, agent_id, capability, json.dumps(challenge_data),
                json.dumps(template.expected_response_pattern), datetime.now(), expires_at
            ))
            conn.commit()
        
        return challenge_id
    
    def submit_challenge_response(self, challenge_id: str, response_data: str) -> bool:
        """Submit response to a challenge and evaluate it."""
        with get_db() as conn:
            # Get challenge
            challenge_row = conn.execute("""
                SELECT * FROM capability_challenges WHERE challenge_id = ?
            """, (challenge_id,)).fetchone()
            
            if not challenge_row:
                raise ValueError("Challenge not found")
            
            # Check if expired
            expires_at = datetime.fromisoformat(challenge_row['expires_at'])
            if datetime.now() > expires_at:
                return False  # Too late
            
            # Update attempts
            new_attempts = challenge_row['attempts'] + 1
            conn.execute("""
                UPDATE capability_challenges SET attempts = ?, response_data = ?
                WHERE challenge_id = ?
            """, (new_attempts, response_data, challenge_id))
            
            # Evaluate response
            challenge_data = json.loads(challenge_row['challenge_data'])
            expected_schema = json.loads(challenge_row['expected_response_schema'])
            
            passed = self._evaluate_response(
                challenge_row['capability'],
                challenge_data,
                response_data,
                expected_schema
            )
            
            if passed:
                # Mark as passed
                conn.execute("""
                    UPDATE capability_challenges 
                    SET passed = 1, verified_at = ? 
                    WHERE challenge_id = ?
                """, (datetime.now(), challenge_id))
                
                # Add to verified capabilities
                agent = get_agent_by_id(challenge_row['agent_id'])
                if agent:
                    verified_caps = agent.capabilities_verified.copy()
                    if challenge_row['capability'] not in verified_caps:
                        verified_caps.append(challenge_row['capability'])
                        
                        conn.execute("""
                            UPDATE agents SET capabilities_verified = ?
                            WHERE agent_id = ?
                        """, (json.dumps(verified_caps), challenge_row['agent_id']))
            
            conn.commit()
            return passed
    
    def get_challenge(self, challenge_id: str) -> Optional[Dict[str, Any]]:
        """Get challenge details for an agent to respond to."""
        with get_db() as conn:
            challenge_row = conn.execute("""
                SELECT * FROM capability_challenges WHERE challenge_id = ?
            """, (challenge_id,)).fetchone()
            
            if not challenge_row:
                return None
            
            challenge_data = json.loads(challenge_row['challenge_data'])
            
            # Don't reveal expected response
            return {
                'challenge_id': challenge_id,
                'capability': challenge_row['capability'],
                'challenge_type': challenge_data.get('type'),
                'instructions': challenge_data.get('instructions'),
                'data': challenge_data.get('data'),
                'time_limit_minutes': challenge_data.get('time_limit_minutes'),
                'attempts_made': challenge_row['attempts'],
                'max_attempts': 3,
                'expires_at': challenge_row['expires_at']
            }
    
    def list_agent_challenges(self, agent_id: str) -> List[Dict[str, Any]]:
        """List all challenges for an agent."""
        with get_db() as conn:
            challenges = conn.execute("""
                SELECT * FROM capability_challenges 
                WHERE agent_id = ? 
                ORDER BY generated_at DESC
            """, (agent_id,)).fetchall()
            
            result = []
            for challenge in challenges:
                result.append({
                    'challenge_id': challenge['challenge_id'],
                    'capability': challenge['capability'],
                    'generated_at': challenge['generated_at'],
                    'expires_at': challenge['expires_at'],
                    'attempts': challenge['attempts'],
                    'passed': bool(challenge['passed']),
                    'verified_at': challenge['verified_at']
                })
            
            return result
    
    def _load_challenge_templates(self) -> Dict[str, List[ChallengeTemplate]]:
        """Load challenge templates for different capabilities."""
        templates = {}
        
        # === RESEARCH CAPABILITIES ===
        templates['research'] = [
            ChallengeTemplate(
                capability='research',
                challenge_type=ChallengeType.RESEARCH,
                template='web_research_task',
                expected_response_pattern='structured_findings',
                scoring_criteria=['source_quality', 'accuracy', 'comprehensiveness'],
                time_limit_minutes=30,
                difficulty_level='basic'
            )
        ]
        
        templates['web-search'] = [
            ChallengeTemplate(
                capability='web-search',
                challenge_type=ChallengeType.RESEARCH,
                template='search_optimization_task',
                expected_response_pattern='search_results_analysis',
                scoring_criteria=['query_effectiveness', 'result_relevance', 'source_evaluation'],
                time_limit_minutes=20,
                difficulty_level='basic'
            )
        ]
        
        # === DATA ANALYSIS CAPABILITIES ===
        templates['data-analysis'] = [
            ChallengeTemplate(
                capability='data-analysis',
                challenge_type=ChallengeType.DATA_ANALYSIS,
                template='dataset_analysis',
                expected_response_pattern='analysis_report',
                scoring_criteria=['statistical_accuracy', 'insight_quality', 'visualization'],
                time_limit_minutes=45,
                difficulty_level='intermediate'
            )
        ]
        
        templates['mes-analysis'] = [
            ChallengeTemplate(
                capability='mes-analysis',
                challenge_type=ChallengeType.DATA_ANALYSIS,
                template='mes_data_interpretation',
                expected_response_pattern='oee_analysis',
                scoring_criteria=['oee_calculation', 'bottleneck_identification', 'recommendations'],
                time_limit_minutes=60,
                difficulty_level='advanced'
            )
        ]
        
        templates['oee-analysis'] = [
            ChallengeTemplate(
                capability='oee-analysis',
                challenge_type=ChallengeType.CALCULATION,
                template='oee_calculation_challenge',
                expected_response_pattern='oee_breakdown',
                scoring_criteria=['formula_accuracy', 'component_identification', 'improvement_suggestions'],
                time_limit_minutes=30,
                difficulty_level='intermediate'
            )
        ]
        
        # === DEVELOPMENT CAPABILITIES ===
        templates['code-generation'] = [
            ChallengeTemplate(
                capability='code-generation',
                challenge_type=ChallengeType.CODE_GENERATION,
                template='api_client_creation',
                expected_response_pattern='working_code',
                scoring_criteria=['functionality', 'code_quality', 'error_handling'],
                time_limit_minutes=45,
                difficulty_level='intermediate'
            )
        ]
        
        # === MARKET/TRADING CAPABILITIES ===
        templates['trading'] = [
            ChallengeTemplate(
                capability='trading',
                challenge_type=ChallengeType.DATA_ANALYSIS,
                template='market_analysis_task',
                expected_response_pattern='trading_strategy',
                scoring_criteria=['market_understanding', 'risk_assessment', 'strategy_viability'],
                time_limit_minutes=40,
                difficulty_level='advanced'
            )
        ]
        
        templates['market-analysis'] = [
            ChallengeTemplate(
                capability='market-analysis',
                challenge_type=ChallengeType.DATA_ANALYSIS,
                template='sector_analysis',
                expected_response_pattern='market_report',
                scoring_criteria=['data_interpretation', 'trend_identification', 'forecasting'],
                time_limit_minutes=35,
                difficulty_level='intermediate'
            )
        ]
        
        # === COMMUNICATION CAPABILITIES ===
        templates['writing'] = [
            ChallengeTemplate(
                capability='writing',
                challenge_type=ChallengeType.WRITING,
                template='technical_documentation',
                expected_response_pattern='structured_document',
                scoring_criteria=['clarity', 'structure', 'technical_accuracy'],
                time_limit_minutes=30,
                difficulty_level='basic'
            )
        ]
        
        templates['report-generation'] = [
            ChallengeTemplate(
                capability='report-generation',
                challenge_type=ChallengeType.WRITING,
                template='executive_summary',
                expected_response_pattern='formatted_report',
                scoring_criteria=['executive_readability', 'data_presentation', 'actionable_insights'],
                time_limit_minutes=40,
                difficulty_level='intermediate'
            )
        ]
        
        # === ORCHESTRATION CAPABILITIES ===
        templates['orchestration'] = [
            ChallengeTemplate(
                capability='orchestration',
                challenge_type=ChallengeType.WORKFLOW,
                template='multi_agent_coordination',
                expected_response_pattern='workflow_plan',
                scoring_criteria=['task_decomposition', 'dependency_management', 'coordination_strategy'],
                time_limit_minutes=30,
                difficulty_level='advanced'
            )
        ]
        
        return templates
    
    def _select_challenge_template(self, capability: str, agent: Agent) -> Optional[ChallengeTemplate]:
        """Select appropriate challenge template for capability and agent context."""
        if capability not in self.challenge_templates:
            return None
        
        templates = self.challenge_templates[capability]
        
        # For now, select first available template
        # TODO: Add intelligence based on agent history, difficulty progression
        return templates[0] if templates else None
    
    def _generate_challenge_data(self, template: ChallengeTemplate, agent: Agent) -> Dict[str, Any]:
        """Generate specific challenge data from template."""
        challenge_generators = {
            'web_research_task': self._generate_research_challenge,
            'search_optimization_task': self._generate_search_challenge,
            'dataset_analysis': self._generate_data_analysis_challenge,
            'mes_data_interpretation': self._generate_mes_challenge,
            'oee_calculation_challenge': self._generate_oee_challenge,
            'api_client_creation': self._generate_code_challenge,
            'market_analysis_task': self._generate_market_challenge,
            'sector_analysis': self._generate_sector_challenge,
            'technical_documentation': self._generate_writing_challenge,
            'executive_summary': self._generate_report_challenge,
            'multi_agent_coordination': self._generate_orchestration_challenge
        }
        
        generator = challenge_generators.get(template.template)
        if not generator:
            raise ValueError(f"No generator for template: {template.template}")
        
        return generator(template, agent)
    
    def _generate_research_challenge(self, template: ChallengeTemplate, agent: Agent) -> Dict[str, Any]:
        """Generate research capability challenge."""
        topics = [
            "autonomous vehicle safety regulations in California",
            "recent advances in quantum computing error correction",
            "sustainable packaging alternatives to plastic wrap",
            "impact of remote work on commercial real estate values",
            "latest FDA approvals for gene therapy treatments"
        ]
        
        import random
        topic = random.choice(topics)
        
        return {
            'type': template.challenge_type.value,
            'instructions': f"Research and provide a comprehensive analysis of: {topic}",
            'requirements': [
                "Find at least 3 authoritative sources",
                "Provide key findings with source citations",
                "Include publication dates and credibility assessment",
                "Summarize main insights in 200-300 words"
            ],
            'time_limit_minutes': template.time_limit_minutes,
            'topic': topic
        }
    
    def _generate_search_challenge(self, template: ChallengeTemplate, agent: Agent) -> Dict[str, Any]:
        """Generate web search optimization challenge."""
        scenarios = [
            "finding manufacturing equipment suppliers in Ohio with ISO certification",
            "locating recent clinical trials for diabetes medication published in 2023",
            "identifying venture capital firms focused on agricultural technology startups"
        ]
        
        import random
        scenario = random.choice(scenarios)
        
        return {
            'type': template.challenge_type.value,
            'instructions': f"Optimize search strategy for: {scenario}",
            'requirements': [
                "Provide 3-5 different search queries you would use",
                "Explain search operators or filters you'd apply",
                "List specific websites or databases you'd target",
                "Describe how you'd verify result quality"
            ],
            'time_limit_minutes': template.time_limit_minutes,
            'scenario': scenario
        }
    
    def _generate_data_analysis_challenge(self, template: ChallengeTemplate, agent: Agent) -> Dict[str, Any]:
        """Generate data analysis challenge with sample dataset."""
        # Generate sample sales data
        import random
        
        sample_data = []
        for month in range(1, 13):
            for product in ['Widget A', 'Widget B', 'Widget C']:
                sample_data.append({
                    'month': month,
                    'product': product,
                    'sales': random.randint(50, 200),
                    'returns': random.randint(0, 10),
                    'cost': random.randint(20, 80)
                })
        
        return {
            'type': template.challenge_type.value,
            'instructions': "Analyze the provided sales data and identify key trends",
            'data': sample_data,
            'requirements': [
                "Calculate monthly revenue and profit trends",
                "Identify best and worst performing products",
                "Determine return rate patterns",
                "Provide 3 actionable business recommendations"
            ],
            'time_limit_minutes': template.time_limit_minutes
        }
    
    def _generate_mes_challenge(self, template: ChallengeTemplate, agent: Agent) -> Dict[str, Any]:
        """Generate MES data interpretation challenge."""
        # Generate sample MES data
        import random
        
        equipment_data = {
            'Line_01': {
                'planned_production_time': 480,  # minutes
                'actual_runtime': 420,
                'good_parts': 850,
                'total_parts': 900,
                'ideal_cycle_time': 0.5,  # minutes per part
                'downtime_events': [
                    {'reason': 'material_shortage', 'duration': 30},
                    {'reason': 'equipment_jam', 'duration': 20},
                    {'reason': 'changeover', 'duration': 10}
                ]
            }
        }
        
        return {
            'type': template.challenge_type.value,
            'instructions': "Analyze MES data and calculate OEE metrics",
            'data': equipment_data,
            'requirements': [
                "Calculate Availability, Performance, and Quality percentages",
                "Calculate Overall Equipment Effectiveness (OEE)",
                "Identify the biggest opportunity for improvement",
                "Recommend specific actions to increase OEE"
            ],
            'time_limit_minutes': template.time_limit_minutes
        }
    
    def _generate_oee_challenge(self, template: ChallengeTemplate, agent: Agent) -> Dict[str, Any]:
        """Generate OEE calculation challenge."""
        import random
        
        scenario_data = {
            'shift_duration_hours': 8,
            'planned_downtime_hours': 0.5,
            'unplanned_downtime_hours': 1.2,
            'total_parts_produced': 1450,
            'defective_parts': 65,
            'ideal_cycle_time_seconds': 18,
            'actual_cycle_time_seconds': 22
        }
        
        return {
            'type': template.challenge_type.value,
            'instructions': "Calculate OEE components from production data",
            'data': scenario_data,
            'requirements': [
                "Show calculation steps for Availability %",
                "Show calculation steps for Performance %", 
                "Show calculation steps for Quality %",
                "Calculate final OEE percentage",
                "Classify OEE level (World Class >85%, Typical 60-85%, Poor <60%)"
            ],
            'time_limit_minutes': template.time_limit_minutes
        }
    
    def _generate_code_challenge(self, template: ChallengeTemplate, agent: Agent) -> Dict[str, Any]:
        """Generate code generation challenge."""
        apis = [
            {
                'name': 'Weather API',
                'endpoint': 'http://api.weather.com/v1/current',
                'params': {'location': 'string', 'apikey': 'string'},
                'response': {'temperature': 'number', 'humidity': 'number', 'conditions': 'string'}
            },
            {
                'name': 'Stock Price API',
                'endpoint': 'https://api.stocks.com/v2/quote',
                'params': {'symbol': 'string', 'token': 'string'},
                'response': {'price': 'number', 'change': 'number', 'volume': 'number'}
            }
        ]
        
        import random
        api_spec = random.choice(apis)
        
        return {
            'type': template.challenge_type.value,
            'instructions': f"Create a Python client for the {api_spec['name']}",
            'api_specification': api_spec,
            'requirements': [
                "Create a class-based client with proper error handling",
                "Include parameter validation",
                "Add basic rate limiting (max 10 requests per minute)",
                "Include example usage in docstrings",
                "Handle common HTTP errors gracefully"
            ],
            'time_limit_minutes': template.time_limit_minutes
        }
    
    def _generate_market_challenge(self, template: ChallengeTemplate, agent: Agent) -> Dict[str, Any]:
        """Generate market analysis challenge."""
        scenarios = [
            {
                'sector': 'Electric Vehicles',
                'recent_news': 'Major automaker announces 50% EV production increase',
                'market_data': {'sector_pe': 28.5, 'growth_rate': 0.15, 'volatility': 0.35}
            },
            {
                'sector': 'Renewable Energy',
                'recent_news': 'Government announces $10B renewable energy tax credits',
                'market_data': {'sector_pe': 22.3, 'growth_rate': 0.12, 'volatility': 0.28}
            }
        ]
        
        import random
        scenario = random.choice(scenarios)
        
        return {
            'type': template.challenge_type.value,
            'instructions': f"Analyze investment opportunity in {scenario['sector']}",
            'scenario': scenario,
            'requirements': [
                "Assess the impact of recent news on sector outlook",
                "Evaluate valuation relative to growth prospects",
                "Identify key risk factors",
                "Provide buy/hold/sell recommendation with rationale"
            ],
            'time_limit_minutes': template.time_limit_minutes
        }
    
    def _generate_sector_challenge(self, template: ChallengeTemplate, agent: Agent) -> Dict[str, Any]:
        """Generate sector analysis challenge."""
        return {
            'type': template.challenge_type.value,
            'instructions': "Analyze the healthcare technology sector",
            'focus_areas': [
                'telehealth adoption trends',
                'AI diagnostic tools market',
                'regulatory environment changes',
                'major player competitive positioning'
            ],
            'requirements': [
                "Identify 3 key growth drivers",
                "Assess regulatory risks and opportunities",
                "Compare sector valuation to historical norms",
                "Forecast sector performance over next 12 months"
            ],
            'time_limit_minutes': template.time_limit_minutes
        }
    
    def _generate_writing_challenge(self, template: ChallengeTemplate, agent: Agent) -> Dict[str, Any]:
        """Generate technical writing challenge."""
        topics = [
            'API rate limiting implementation guide',
            'Database backup and recovery procedures',
            'User authentication security best practices'
        ]
        
        import random
        topic = random.choice(topics)
        
        return {
            'type': template.challenge_type.value,
            'instructions': f"Write technical documentation for: {topic}",
            'target_audience': 'software developers with 2-5 years experience',
            'requirements': [
                "Include clear step-by-step procedures",
                "Add code examples where applicable",
                "Structure with proper headings and sections",
                "Write 500-800 words",
                "Include troubleshooting section"
            ],
            'time_limit_minutes': template.time_limit_minutes
        }
    
    def _generate_report_challenge(self, template: ChallengeTemplate, agent: Agent) -> Dict[str, Any]:
        """Generate report generation challenge."""
        import random
        
        metrics = {
            'q3_revenue': 2.4,  # millions
            'q3_growth': 0.08,  # 8%
            'customer_count': 1250,
            'churn_rate': 0.05,  # 5%
            'product_launches': 2,
            'support_tickets': 340
        }
        
        return {
            'type': template.challenge_type.value,
            'instructions': "Create an executive summary from Q3 business metrics",
            'data': metrics,
            'audience': 'C-level executives and board members',
            'requirements': [
                "Write executive summary (150-200 words)",
                "Highlight key achievements and concerns",
                "Include data-driven insights",
                "Provide specific recommendations",
                "Use professional business language"
            ],
            'time_limit_minutes': template.time_limit_minutes
        }
    
    def _generate_orchestration_challenge(self, template: ChallengeTemplate, agent: Agent) -> Dict[str, Any]:
        """Generate multi-agent orchestration challenge."""
        scenario = {
            'task': 'competitive market research project',
            'available_agents': [
                {'name': 'researcher', 'capabilities': ['web-search', 'data-collection']},
                {'name': 'analyst', 'capabilities': ['data-analysis', 'statistical-modeling']},
                {'name': 'writer', 'capabilities': ['report-generation', 'executive-summary']}
            ],
            'deadline': '5 business days',
            'deliverable': 'comprehensive market analysis report'
        }
        
        return {
            'type': template.challenge_type.value,
            'instructions': "Design coordination plan for multi-agent research project",
            'scenario': scenario,
            'requirements': [
                "Break down project into specific agent tasks",
                "Define task dependencies and sequence",
                "Specify deliverable handoffs between agents",
                "Include quality checkpoints and validation steps",
                "Estimate timeline with buffer for revisions"
            ],
            'time_limit_minutes': template.time_limit_minutes
        }
    
    def _evaluate_response(self, capability: str, challenge_data: Dict[str, Any], 
                          response_data: str, expected_schema: str) -> bool:
        """Evaluate agent response to challenge."""
        evaluators = {
            'research': self._evaluate_research_response,
            'web-search': self._evaluate_search_response,
            'data-analysis': self._evaluate_data_analysis_response,
            'mes-analysis': self._evaluate_mes_response,
            'oee-analysis': self._evaluate_oee_response,
            'code-generation': self._evaluate_code_response,
            'trading': self._evaluate_trading_response,
            'market-analysis': self._evaluate_market_response,
            'writing': self._evaluate_writing_response,
            'report-generation': self._evaluate_report_response,
            'orchestration': self._evaluate_orchestration_response
        }
        
        evaluator = evaluators.get(capability)
        if not evaluator:
            # Generic evaluation - check response length and structure
            return len(response_data.strip()) >= 100
        
        return evaluator(challenge_data, response_data)
    
    def _evaluate_research_response(self, challenge_data: Dict[str, Any], response: str) -> bool:
        """Evaluate research challenge response."""
        # Check for sources, citations, structure
        indicators = [
            'http' in response.lower(),  # URLs present
            'source' in response.lower() or 'citation' in response.lower(),
            len(response.split()) >= 150,  # Sufficient length
            response.count('\n') >= 3  # Some structure
        ]
        return sum(indicators) >= 3
    
    def _evaluate_search_response(self, challenge_data: Dict[str, Any], response: str) -> bool:
        """Evaluate search optimization response."""
        indicators = [
            'query' in response.lower() or 'search' in response.lower(),
            'filter' in response.lower() or 'operator' in response.lower(),
            'site:' in response or 'filetype:' in response or '"' in response,
            len(response.split()) >= 100
        ]
        return sum(indicators) >= 3
    
    def _evaluate_data_analysis_response(self, challenge_data: Dict[str, Any], response: str) -> bool:
        """Evaluate data analysis response."""
        indicators = [
            any(month in response for month in ['january', 'february', 'march', 'jan', 'feb', 'mar']),
            'revenue' in response.lower() or 'profit' in response.lower(),
            'trend' in response.lower(),
            'recommendation' in response.lower(),
            len(response.split()) >= 150
        ]
        return sum(indicators) >= 4
    
    def _evaluate_mes_response(self, challenge_data: Dict[str, Any], response: str) -> bool:
        """Evaluate MES analysis response."""
        indicators = [
            'oee' in response.lower(),
            'availability' in response.lower(),
            'performance' in response.lower(),
            'quality' in response.lower(),
            any(str(i) in response for i in range(50, 101)),  # Percentage values
            'improvement' in response.lower()
        ]
        return sum(indicators) >= 5
    
    def _evaluate_oee_response(self, challenge_data: Dict[str, Any], response: str) -> bool:
        """Evaluate OEE calculation response."""
        data = challenge_data.get('data', {})
        
        # Calculate expected values
        planned_time = (data['shift_duration_hours'] - data['planned_downtime_hours']) * 60
        actual_time = planned_time - (data['unplanned_downtime_hours'] * 60)
        availability = (actual_time / planned_time) * 100
        
        good_parts = data['total_parts_produced'] - data['defective_parts']
        quality = (good_parts / data['total_parts_produced']) * 100
        
        expected_availability = f"{availability:.0f}"
        expected_quality = f"{quality:.0f}"
        
        indicators = [
            expected_availability in response,
            expected_quality in response,
            'availability' in response.lower(),
            'performance' in response.lower(),
            'quality' in response.lower(),
            'oee' in response.lower()
        ]
        return sum(indicators) >= 4
    
    def _evaluate_code_response(self, challenge_data: Dict[str, Any], response: str) -> bool:
        """Evaluate code generation response."""
        indicators = [
            'class' in response and 'def' in response,  # Class-based structure
            'try:' in response and 'except' in response,  # Error handling
            'import' in response,  # Imports
            'self' in response,  # Instance methods
            len(response) >= 300,  # Substantial code
            '#' in response or '"""' in response  # Comments/docstrings
        ]
        return sum(indicators) >= 4
    
    def _evaluate_trading_response(self, challenge_data: Dict[str, Any], response: str) -> bool:
        """Evaluate trading analysis response."""
        indicators = [
            'buy' in response.lower() or 'sell' in response.lower() or 'hold' in response.lower(),
            'risk' in response.lower(),
            'valuation' in response.lower() or 'pe' in response.lower(),
            'growth' in response.lower(),
            len(response.split()) >= 100
        ]
        return sum(indicators) >= 4
    
    def _evaluate_market_response(self, challenge_data: Dict[str, Any], response: str) -> bool:
        """Evaluate market analysis response."""
        indicators = [
            'sector' in response.lower() or 'industry' in response.lower(),
            'growth' in response.lower() or 'trend' in response.lower(),
            'forecast' in response.lower() or 'outlook' in response.lower(),
            'risk' in response.lower() or 'opportunity' in response.lower(),
            len(response.split()) >= 120
        ]
        return sum(indicators) >= 4
    
    def _evaluate_writing_response(self, challenge_data: Dict[str, Any], response: str) -> bool:
        """Evaluate technical writing response."""
        word_count = len(response.split())
        indicators = [
            500 <= word_count <= 1000,  # Appropriate length
            response.count('\n') >= 5,  # Structure with sections
            'step' in response.lower() or 'procedure' in response.lower(),
            'example' in response.lower() or 'code' in response.lower(),
            'troubleshoot' in response.lower() or 'error' in response.lower()
        ]
        return sum(indicators) >= 3
    
    def _evaluate_report_response(self, challenge_data: Dict[str, Any], response: str) -> bool:
        """Evaluate report generation response."""
        word_count = len(response.split())
        indicators = [
            100 <= word_count <= 300,  # Executive summary length
            'q3' in response.lower() or 'quarter' in response.lower(),
            'revenue' in response.lower() or 'growth' in response.lower(),
            'recommend' in response.lower() or 'suggest' in response.lower(),
            response.count('%') >= 1  # Percentages for metrics
        ]
        return sum(indicators) >= 4
    
    def _evaluate_orchestration_response(self, challenge_data: Dict[str, Any], response: str) -> bool:
        """Evaluate orchestration plan response."""
        indicators = [
            'researcher' in response.lower() and 'analyst' in response.lower(),
            'task' in response.lower() or 'step' in response.lower(),
            'dependency' in response.lower() or 'sequence' in response.lower(),
            'timeline' in response.lower() or 'schedule' in response.lower(),
            'quality' in response.lower() or 'review' in response.lower(),
            len(response.split()) >= 150
        ]
        return sum(indicators) >= 4
    
    def _load_verification_rules(self) -> Dict[str, Any]:
        """Load verification rules for response evaluation."""
        # TODO: Implement more sophisticated verification rules
        return {}


# Global challenger instance
capability_challenger = CapabilityChallenger()