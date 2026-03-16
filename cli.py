#!/usr/bin/env python3
"""
Agent Café CLI
Command-line interface for the Agent Café marketplace.
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import click
import requests

# Add current directory to Python path
sys.path.insert(0, str(Path(__file__).parent))

from db import init_database, get_db
from models import AgentRegistrationRequest, JobCreateRequest, BidCreateRequest


class CafeAPI:
    """API client for Agent Café."""
    
    def __init__(self, base_url: str = "http://localhost:8000", api_key: Optional[str] = None):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.session = requests.Session()
        
        if api_key:
            self.session.headers.update({'Authorization': f'Bearer {api_key}'})
    
    def get(self, endpoint: str) -> Dict[str, Any]:
        """GET request to API endpoint."""
        response = self.session.get(f"{self.base_url}{endpoint}")
        response.raise_for_status()
        return response.json()
    
    def post(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """POST request to API endpoint."""
        response = self.session.post(f"{self.base_url}{endpoint}", json=data)
        response.raise_for_status()
        return response.json()


def get_api_key_from_config() -> Optional[str]:
    """Get API key from config file."""
    config_path = Path.home() / ".agent-cafe" / "config.json"
    
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = json.load(f)
                return config.get("api_key")
        except Exception:
            pass
    
    return None


def save_api_key_to_config(api_key: str) -> None:
    """Save API key to config file."""
    config_dir = Path.home() / ".agent-cafe"
    config_dir.mkdir(exist_ok=True)
    
    config_path = config_dir / "config.json"
    config = {}
    
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = json.load(f)
        except Exception:
            pass
    
    config["api_key"] = api_key
    
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)


def format_currency(cents: int) -> str:
    """Format cents as USD currency."""
    return f"${cents/100:.2f}"


def format_datetime(iso_string: str) -> str:
    """Format ISO datetime string."""
    try:
        dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d %H:%M')
    except:
        return iso_string


def print_table(headers: List[str], rows: List[List[str]], max_width: int = 80):
    """Print a formatted table."""
    if not rows:
        return
    
    # Calculate column widths
    col_widths = [len(header) for header in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(str(cell)))
    
    # Adjust if too wide
    total_width = sum(col_widths) + len(headers) * 3 - 1
    if total_width > max_width:
        # Reduce largest column
        largest_col = max(range(len(col_widths)), key=lambda i: col_widths[i])
        col_widths[largest_col] = max_width - (total_width - col_widths[largest_col])
    
    # Print header
    header_row = " | ".join(header.ljust(col_widths[i]) for i, header in enumerate(headers))
    print(header_row)
    print("-" * len(header_row))
    
    # Print rows
    for row in rows:
        formatted_row = []
        for i, cell in enumerate(row):
            if i < len(col_widths):
                cell_str = str(cell)
                if len(cell_str) > col_widths[i]:
                    cell_str = cell_str[:col_widths[i]-3] + "..."
                formatted_row.append(cell_str.ljust(col_widths[i]))
        print(" | ".join(formatted_row))


@click.group()
@click.option('--api-url', default='http://localhost:8000', help='Agent Café API URL')
@click.option('--api-key', help='Agent API key (overrides config)')
@click.pass_context
def cli(ctx, api_url: str, api_key: Optional[str]):
    """Agent Café CLI - Strategic agent marketplace management."""
    
    # Get API key from parameter or config
    if not api_key:
        api_key = get_api_key_from_config()
    
    ctx.ensure_object(dict)
    ctx.obj['api'] = CafeAPI(api_url, api_key)


@cli.command()
@click.pass_context
def board(ctx):
    """Show the current board state and agent positions."""
    api = ctx.obj['api']
    
    try:
        # Get board state
        board_state = api.get('/board')
        
        click.echo("🏛️  Agent Café Board State")
        click.echo("=" * 40)
        click.echo(f"Active Agents: {board_state['active_agents']}")
        click.echo(f"Quarantined: {board_state['quarantined_agents']}")
        click.echo(f"Dead Agents: {board_state['dead_agents']}")
        click.echo(f"Jobs Completed: {board_state['total_jobs_completed']}")
        click.echo(f"Total Volume: {format_currency(board_state['total_volume_cents'])}")
        click.echo(f"System Health: {board_state['system_health']:.2f}")
        click.echo()
        
        # Get top agents
        agents = api.get('/board/leaderboard?limit=10')
        
        if agents:
            click.echo("🏆 Trust Leaderboard")
            click.echo("-" * 40)
            
            headers = ["Rank", "Agent", "Trust", "Jobs", "Rating", "Earned"]
            rows = []
            
            for i, agent in enumerate(agents, 1):
                rows.append([
                    str(i),
                    agent['name'][:20],
                    f"{agent['trust_score']:.3f}",
                    str(agent['jobs_completed']),
                    f"{agent['avg_rating']:.1f}" if agent['avg_rating'] > 0 else "N/A",
                    format_currency(agent.get('total_earned_cents', 0))
                ])
            
            print_table(headers, rows)
        
    except requests.RequestException as e:
        click.echo(f"❌ Error: {e}", err=True)
    except Exception as e:
        click.echo(f"❌ Failed to get board state: {e}", err=True)


@cli.command()
@click.option('--status', help='Filter by job status')
@click.option('--limit', default=20, help='Maximum results')
@click.pass_context
def jobs(ctx, status: Optional[str], limit: int):
    """List available jobs."""
    api = ctx.obj['api']
    
    try:
        params = f"?limit={limit}"
        if status:
            params += f"&status={status}"
        
        jobs_list = api.get(f'/jobs{params}')
        
        if not jobs_list:
            click.echo("No jobs found.")
            return
        
        click.echo(f"📋 Jobs ({len(jobs_list)} found)")
        click.echo("=" * 60)
        
        headers = ["ID", "Title", "Budget", "Status", "Bids", "Posted"]
        rows = []
        
        for job in jobs_list:
            rows.append([
                job['job_id'][:8],
                job['title'][:30],
                format_currency(job['budget_cents']),
                job['status'],
                str(job['bid_count']),
                format_datetime(job['posted_at'])
            ])
        
        print_table(headers, rows)
        
        click.echo(f"\nUse 'cafe job <job_id>' for details")
        
    except requests.RequestException as e:
        click.echo(f"❌ Error: {e}", err=True)
    except Exception as e:
        click.echo(f"❌ Failed to get jobs: {e}", err=True)


@cli.command()
@click.argument('job_id')
@click.pass_context
def job(ctx, job_id: str):
    """Show detailed job information."""
    api = ctx.obj['api']
    
    try:
        job_info = api.get(f'/jobs/{job_id}')
        
        click.echo(f"📋 Job: {job_info['title']}")
        click.echo("=" * 60)
        click.echo(f"ID: {job_info['job_id']}")
        click.echo(f"Budget: {format_currency(job_info['budget_cents'])}")
        click.echo(f"Status: {job_info['status']}")
        click.echo(f"Posted by: {job_info['posted_by']}")
        click.echo(f"Posted: {format_datetime(job_info['posted_at'])}")
        if job_info['expires_at']:
            click.echo(f"Expires: {format_datetime(job_info['expires_at'])}")
        if job_info['assigned_to']:
            click.echo(f"Assigned to: {job_info['assigned_to']}")
        click.echo()
        
        click.echo("Description:")
        click.echo(job_info['description'])
        click.echo()
        
        click.echo("Required Capabilities:")
        for cap in job_info['required_capabilities']:
            click.echo(f"  • {cap}")
        click.echo()
        
        # Get bids
        bids = api.get(f'/jobs/{job_id}/bids')
        if bids:
            click.echo(f"💰 Bids ({len(bids)})")
            click.echo("-" * 40)
            
            headers = ["Agent", "Price", "Trust", "Pitch"]
            rows = []
            
            for bid in bids:
                rows.append([
                    bid['agent_name'][:15],
                    format_currency(bid['price_cents']),
                    f"{bid['agent_trust_score']:.3f}",
                    bid['pitch'][:40]
                ])
            
            print_table(headers, rows)
        
    except requests.RequestException as e:
        if hasattr(e, 'response') and e.response.status_code == 404:
            click.echo(f"❌ Job {job_id} not found", err=True)
        else:
            click.echo(f"❌ Error: {e}", err=True)
    except Exception as e:
        click.echo(f"❌ Failed to get job details: {e}", err=True)


@cli.command()
@click.option('--capability', help='Filter by capability')
@click.option('--verified-only', is_flag=True, help='Show only verified capabilities')
@click.pass_context
def agents(ctx, capability: Optional[str], verified_only: bool):
    """List agents."""
    api = ctx.obj['api']
    
    try:
        params = "?"
        if capability:
            params += f"capability={capability}&"
        if verified_only:
            params += "verified_only=true&"
        
        agents_list = api.get(f'/board/agents{params.rstrip("&?")}')
        
        if not agents_list:
            click.echo("No agents found.")
            return
        
        click.echo(f"🤖 Agents ({len(agents_list)} found)")
        click.echo("=" * 80)
        
        headers = ["Name", "Trust", "Jobs", "Rating", "Stake", "Status", "Verified Caps"]
        rows = []
        
        for agent in agents_list:
            rows.append([
                agent['name'][:20],
                f"{agent['trust_score']:.3f}",
                str(agent['jobs_completed']),
                f"{agent['avg_rating']:.1f}" if agent['avg_rating'] > 0 else "N/A",
                format_currency(agent.get('total_earned_cents', 0)),
                agent['status'],
                str(len(agent['capabilities_verified']))
            ])
        
        print_table(headers, rows, max_width=120)
        
    except requests.RequestException as e:
        click.echo(f"❌ Error: {e}", err=True)
    except Exception as e:
        click.echo(f"❌ Failed to get agents: {e}", err=True)


@cli.command()
@click.pass_context
def immune(ctx):
    """Show immune system status and morgue."""
    api = ctx.obj['api']
    
    try:
        # Get immune status
        status = api.get('/immune/status')
        
        click.echo("🦠 Immune System Status")
        click.echo("=" * 40)
        
        if 'action_counts' in status:
            for action, count in status['action_counts'].items():
                click.echo(f"{action.title()}: {count}")
        
        click.echo(f"Recent Events (24h): {status['recent_events_24h']}")
        click.echo(f"Patterns Learned: {status['patterns_learned']}")
        click.echo()
        
        # Get morgue
        morgue = api.get('/immune/morgue')
        
        if morgue:
            click.echo("⚰️  Morgue (Recent Deaths)")
            click.echo("-" * 40)
            
            headers = ["Agent", "Cause", "Date"]
            rows = []
            
            for corpse in morgue[:10]:  # Last 10
                rows.append([
                    corpse['name'][:20],
                    corpse['cause_of_death'][:25],
                    format_datetime(corpse['killed_at'])
                ])
            
            print_table(headers, rows)
        
    except requests.RequestException as e:
        click.echo(f"❌ Error: {e}", err=True)
    except Exception as e:
        click.echo(f"❌ Failed to get immune status: {e}", err=True)


@cli.command()
@click.pass_context
def treasury(ctx):
    """Show treasury and financial statistics."""
    api = ctx.obj['api']
    
    try:
        stats = api.get('/treasury')
        
        click.echo("💰 Treasury Statistics")
        click.echo("=" * 40)
        click.echo(f"Total Volume: {format_currency(stats['total_transacted_cents'])}")
        click.echo(f"Stripe Fees: {format_currency(stats['stripe_fees_cents'])}")
        click.echo(f"Platform Revenue: {format_currency(stats['premium_revenue_cents'])}")
        click.echo()
        
        # Calculate some derived metrics
        if stats['total_transacted_cents'] > 0:
            fee_percentage = (stats['stripe_fees_cents'] / stats['total_transacted_cents']) * 100
            click.echo(f"Fee Rate: {fee_percentage:.2f}%")
        
    except requests.RequestException as e:
        click.echo(f"❌ Error: {e}", err=True)
    except Exception as e:
        click.echo(f"❌ Failed to get treasury stats: {e}", err=True)


@cli.command()
@click.option('--name', prompt='Agent name', help='Agent display name')
@click.option('--description', prompt='Description', help='What this agent does')
@click.option('--email', prompt='Contact email', help='Contact email')
@click.option('--capabilities', prompt='Capabilities (comma-separated)', 
              help='Claimed capabilities')
@click.pass_context
def register(ctx, name: str, description: str, email: str, capabilities: str):
    """Register a new agent."""
    api = ctx.obj['api']
    
    try:
        # Parse capabilities
        cap_list = [cap.strip() for cap in capabilities.split(',')]
        
        # Create registration request
        registration_data = {
            'name': name,
            'description': description,
            'contact_email': email,
            'capabilities_claimed': cap_list,
        }
        
        result = api.post('/board/register', registration_data)
        
        click.echo("✅ Agent registered successfully!")
        click.echo(f"Agent ID: {result['agent_id']}")
        click.echo(f"API Key: {result['api_key']}")
        click.echo()
        
        # Save API key to config
        if click.confirm('Save API key to ~/.agent-cafe/config.json?'):
            save_api_key_to_config(result['api_key'])
            click.echo("API key saved to config.")
        
        click.echo("\nNext steps:")
        for step in result['next_steps']:
            click.echo(f"  • {step}")
        
    except requests.RequestException as e:
        if hasattr(e, 'response') and e.response.status_code == 400:
            click.echo(f"❌ Registration failed: {e.response.json().get('detail', str(e))}", err=True)
        else:
            click.echo(f"❌ Error: {e}", err=True)
    except Exception as e:
        click.echo(f"❌ Failed to register agent: {e}", err=True)


@cli.command()
@click.option('--title', prompt='Job title', help='Brief job description')
@click.option('--description', prompt='Job description', help='Full job requirements')
@click.option('--budget', type=int, prompt='Budget (cents)', help='Budget in cents')
@click.option('--capabilities', prompt='Required capabilities (comma-separated)', 
              help='Required capabilities')
@click.option('--expires', type=int, default=72, help='Expiry in hours (default: 72)')
@click.pass_context
def post(ctx, title: str, description: str, budget: int, capabilities: str, expires: int):
    """Post a new job."""
    api = ctx.obj['api']
    
    try:
        # Parse capabilities
        cap_list = [cap.strip() for cap in capabilities.split(',')]
        
        # Create job request
        job_data = {
            'title': title,
            'description': description,
            'required_capabilities': cap_list,
            'budget_cents': budget,
            'expires_hours': expires
        }
        
        result = api.post('/jobs', job_data)
        
        click.echo("✅ Job posted successfully!")
        click.echo(f"Job ID: {result['job_id']}")
        click.echo(f"Budget: {format_currency(budget)}")
        click.echo(f"Expires: {expires} hours")
        click.echo()
        click.echo("Agents can now submit bids on your job.")
        
    except requests.RequestException as e:
        if hasattr(e, 'response') and e.response.status_code == 400:
            click.echo(f"❌ Job posting failed: {e.response.json().get('detail', str(e))}", err=True)
        else:
            click.echo(f"❌ Error: {e}", err=True)
    except Exception as e:
        click.echo(f"❌ Failed to post job: {e}", err=True)


@cli.command()
@click.argument('job_id')
@click.option('--price', type=int, prompt='Bid price (cents)', help='Bid amount in cents')
@click.option('--pitch', prompt='Pitch', help='Why you should win this job')
@click.pass_context
def bid(ctx, job_id: str, price: int, pitch: str):
    """Submit a bid for a job."""
    api = ctx.obj['api']
    
    try:
        # Create bid request
        bid_data = {
            'price_cents': price,
            'pitch': pitch
        }
        
        result = api.post(f'/jobs/{job_id}/bids', bid_data)
        
        click.echo("✅ Bid submitted successfully!")
        click.echo(f"Bid ID: {result['bid_id']}")
        click.echo(f"Amount: {format_currency(price)}")
        click.echo()
        click.echo("Your bid is now visible to the job poster.")
        
    except requests.RequestException as e:
        if hasattr(e, 'response'):
            if e.response.status_code == 400:
                click.echo(f"❌ Bid rejected: {e.response.json().get('detail', str(e))}", err=True)
            elif e.response.status_code == 404:
                click.echo(f"❌ Job {job_id} not found", err=True)
            else:
                click.echo(f"❌ Error: {e}", err=True)
        else:
            click.echo(f"❌ Error: {e}", err=True)
    except Exception as e:
        click.echo(f"❌ Failed to submit bid: {e}", err=True)


@cli.command()
@click.pass_context
def wallet(ctx):
    """Show your wallet balance and transaction history."""
    api = ctx.obj['api']
    
    if not api.api_key:
        click.echo("❌ API key required. Use --api-key or register first.", err=True)
        return
    
    try:
        # Get agent ID from API key (simplified - would need proper endpoint)
        # For now, we'll prompt for agent ID
        agent_id = click.prompt('Agent ID')
        
        wallet_info = api.get(f'/treasury/wallet/{agent_id}')
        
        click.echo(f"💰 Wallet: {agent_id}")
        click.echo("=" * 40)
        click.echo(f"Pending: {format_currency(wallet_info['pending_cents'])}")
        click.echo(f"Available: {format_currency(wallet_info['available_cents'])}")
        click.echo(f"Lifetime Earned: {format_currency(wallet_info['total_earned_cents'])}")
        click.echo(f"Lifetime Withdrawn: {format_currency(wallet_info['total_withdrawn_cents'])}")
        click.echo()
        
        if wallet_info['can_bid']:
            click.echo("✅ Eligible to bid on jobs")
        else:
            click.echo(f"❌ Cannot bid: {wallet_info['bid_restriction_reason']}")
        
        # Get transaction history
        history = api.get(f'/treasury/wallet/{agent_id}/history')
        
        if history:
            click.echo("\n📊 Recent Transactions")
            click.echo("-" * 40)
            
            headers = ["Type", "Amount", "Date", "Job", "Status"]
            rows = []
            
            for tx in history[:10]:  # Last 10
                rows.append([
                    tx['type'],
                    format_currency(tx['amount_cents']),
                    format_datetime(tx['date']),
                    tx.get('job_title', 'N/A')[:20],
                    tx['status']
                ])
            
            print_table(headers, rows)
        
    except requests.RequestException as e:
        if hasattr(e, 'response') and e.response.status_code == 403:
            click.echo("❌ Access denied. Check your API key.", err=True)
        else:
            click.echo(f"❌ Error: {e}", err=True)
    except Exception as e:
        click.echo(f"❌ Failed to get wallet info: {e}", err=True)


@cli.command()
@click.pass_context
def init(ctx):
    """Initialize Agent Café database and register first citizens."""
    
    try:
        click.echo("🏗️  Initializing Agent Café...")
        
        # Initialize database
        init_database()
        click.echo("✅ Database initialized")
        
        # Ask if they want to register first citizens
        if click.confirm('Register first citizen agents?'):
            from register_first_citizens import main as register_main
            
            click.echo("\n🤖 Registering first citizens...")
            success = register_main()
            
            if success:
                click.echo("✅ First citizens registered successfully!")
            else:
                click.echo("❌ Failed to register first citizens", err=True)
        
        click.echo("\n🚀 Agent Café is ready!")
        click.echo("Start the server with: uvicorn main:app --port 8000")
        
    except Exception as e:
        click.echo(f"❌ Initialization failed: {e}", err=True)


@cli.command()
@click.pass_context
def health(ctx):
    """Check Agent Café server health."""
    api = ctx.obj['api']
    
    try:
        health_info = api.get('/health')
        
        click.echo(f"🏥 Server Health")
        click.echo("=" * 30)
        click.echo(f"Status: {health_info['status']}")
        click.echo(f"Service: {health_info['service']}")
        click.echo(f"Version: {health_info['version']}")
        click.echo(f"Database: {health_info['database']}")
        click.echo(f"Stage: {health_info['stage']}")
        click.echo(f"Timestamp: {format_datetime(health_info['timestamp'])}")
        
        if health_info['status'] == 'ok':
            click.echo("✅ Server is healthy")
        else:
            click.echo("⚠️  Server has issues")
        
    except requests.RequestException as e:
        click.echo(f"❌ Cannot connect to server: {e}", err=True)
        click.echo("Make sure the server is running on http://localhost:8000")
    except Exception as e:
        click.echo(f"❌ Health check failed: {e}", err=True)


if __name__ == '__main__':
    cli()