"""
Agent X1 - Main Entry Point.

Main entry point for the LLM agent system supporting multiple providers:
- Kimi (OpenAI-compatible API)
- Anthropic-style API
- [Future: OpenAI, Gemini, etc.]

Usage:
    # Interactive mode (default)
    python main.py
    
    # Single query
    python main.py --query "What's the weather in Beijing?"
    
    # With specific config
    python main.py --config config/my_config.yaml
    
    # Initialize config file
    python main.py --init-config

Environment Variables:
    LLM_PROVIDER: Provider type (kimi, anthropic, openai)
    KIMI_API_KEY / ANTHROPIC_API_KEY: API authentication
    KIMI_MODEL / ANTHROPIC_MODEL: Model identifier
    LOG_LEVEL: Logging level (DEBUG, INFO, WARNING, ERROR)
"""

import sys
import argparse
import re
import os
from typing import Optional

# Terminal handling for macOS/Linux
def setup_terminal():
    """Configure terminal for proper Unicode and line editing support."""
    if sys.platform == 'darwin':  # macOS
        os.environ.setdefault('LC_ALL', 'en_US.UTF-8')
        os.environ.setdefault('LANG', 'en_US.UTF-8')
        try:
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except:
            pass

setup_terminal()

# Try to import prompt_toolkit for better input handling
try:
    from prompt_toolkit import prompt as pt_prompt
    from prompt_toolkit.patch_stdout import patch_stdout
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False

from src.core import load_config, AppConfig, create_default_config_file
from src.core.session_manager import init_session_manager, end_current_session, get_session_manager
from src.util.logger import get_logger, setup_logging
from src.engine import create_engine, ProviderType
from src.tools import ALL_TOOLS, TOOL_CATEGORIES_MAP
from src.skills import SkillRegistry, SkillContextManager

logger = get_logger(__name__)


def safe_input(prompt: str) -> str:
    """
    Safe input function with proper UTF-8 and escape sequence handling.
    Uses prompt_toolkit if available, otherwise standard input with filtering.
    """
    if PROMPT_TOOLKIT_AVAILABLE:
        try:
            with patch_stdout():
                return pt_prompt(prompt, multiline=False).strip()
        except Exception:
            pass  # Fall through to fallback
    
    # Fallback: use standard input with ANSI filtering
    try:
        line = input(prompt)
        # Filter ANSI escape sequences
        ansi_escape = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        line = ansi_escape.sub('', line)
        line = re.sub(r'\^\[\[[0-9]*[A-D]', '', line)
        return line.strip()
    except (KeyboardInterrupt, EOFError):
        raise
    except:
        return ""


def create_and_configure_engine(config: AppConfig):
    """
    Create and configure engine with all tools.
    
    Args:
        config: Application configuration
        
    Returns:
        Configured engine instance
    """
    # Create engine from config
    engine = create_engine(
        provider=ProviderType(config.llm.provider),
        api_key=config.llm.api_key,
        base_url=config.llm.base_url,
        model=config.llm.model,
        temperature=config.llm.temperature,
        max_tokens=config.llm.max_tokens,
        timeout=config.llm.timeout,
        max_iterations=config.llm.max_iterations,
        system_prompt=config.llm.system_prompt
    )
    
    # Register all tools
    for tool in ALL_TOOLS:
        try:
            engine.register_tool(tool)
        except ValueError as e:
            logger.warning(f"Failed to register tool {tool.name}: {e}")
    
    logger.info(f"Engine configured with {len(engine.tools)} tools: {list(engine.tools.keys())}")
    
    # --- Skill framework bootstrap ---
    project_root = os.path.dirname(os.path.abspath(__file__))
    skills_root = os.path.join(project_root, "skills")
    registry = SkillRegistry(skills_root)
    skill_count = registry.discover()
    logger.info(f"Discovered {skill_count} skill(s) from {skills_root}")

    ctx = SkillContextManager(registry)
    engine.set_skill_context(ctx)
    engine.set_tool_categories(TOOL_CATEGORIES_MAP)
    
    return engine


def run_interactive_mode(engine) -> None:
    """
    Run interactive chat mode.
    
    Args:
        engine: Configured engine instance
    """
    print("\n" + "=" * 60)
    print("🤖 Agent X1 - Interactive Mode")
    print("=" * 60)
    print(f"Provider: {engine.config.provider.value}")
    print(f"Model: {engine.config.model}")
    print("\nCommands:")
    print("  /help     - Show help")
    print("  /tools    - List registered tools")
    print("  /skills   - List available skills")
    print("  /skill    - Activate a skill (e.g. /skill recommendation_research)")
    print("  /clear    - Clear conversation history")
    print("  /history  - Show conversation history")
    print("  /quit     - Exit")
    print("-" * 60)
    
    while True:
        try:
            user_input = safe_input("\n👤 You: ")
            
            if not user_input:
                continue
            
            # Handle commands
            cmd = user_input.lower()
            
            if cmd in ['/quit', '/exit', 'quit', 'exit']:
                print("\n👋 Goodbye!")
                # End session with summary
                end_current_session("User exited normally")
                break
            
            if cmd == '/help':
                print("\n📖 Commands:")
                print("  /help     - Show help")
                print("  /tools    - List tools")
                print("  /skills   - List available skills")
                print("  /skill    - Activate a skill")
                print("  /clear    - Clear history")
                print("  /history  - Show history")
                print("  /quit     - Exit")
                continue
            
            if cmd == '/tools':
                print("\n🔧 Registered Tools:")
                for name, tool in engine.tools.items():
                    print(f"  • {name}: {tool.description[:50]}...")
                continue
            
            if cmd == '/skills':
                ctx = engine.skill_context
                if ctx and ctx.registry:
                    names = ctx.registry.list_names()
                    if names:
                        print("\n🎯 Available Skills:")
                        for sn in names:
                            summary = ctx.registry.get_summary(sn)
                            desc = summary.description[:60] if summary else ""
                            print(f"  • {sn}: {desc}")
                    else:
                        print("\n⚠️  No skills discovered. Add SKILL.md files to skills/ directory.")
                else:
                    print("\n⚠️  Skill framework not initialized.")
                continue
            
            if user_input.lower().startswith('/skill '):
                skill_name = user_input[7:].strip()
                ctx = engine.skill_context
                if ctx:
                    if ctx.activate_skill(skill_name):
                        print(f"\n✅ Skill '{skill_name}' activated.")
                        if ctx.workspace:
                            print(f"   Workspace: {ctx.workspace.workspace_dir}")
                    else:
                        print(f"\n❌ Skill '{skill_name}' not found. Use /skills to list.")
                else:
                    print("\n⚠️  Skill framework not initialized.")
                continue
            
            if cmd == '/clear':
                engine.clear_history()
                print("\n🧹 Conversation history cleared.")
                continue
            
            if cmd == '/history':
                history = engine.get_conversation_history()
                print(f"\n📜 History ({len(history)} messages):")
                for i, msg in enumerate(history[-10:], 1):  # Show last 10
                    role = msg.get('role', 'unknown')
                    content = msg.get('content', '')
                    if content:
                        preview = content[:50] + "..." if len(content) > 50 else content
                        print(f"  {i}. [{role}] {preview}")
                    elif msg.get('tool_calls'):
                        tool_names = [tc['function']['name'] for tc in msg['tool_calls']]
                        print(f"  {i}. [{role}] Tools: {', '.join(tool_names)}")
                continue
            
            # Process query
            print("\n🤖 Assistant: ", end="", flush=True)
            response = engine.chat(user_input)
            print(response)
            
        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            # End session on interrupt
            end_current_session("User interrupted (Ctrl+C)")
            break
        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
            print(f"\n❌ Error: {e}")


def run_single_query(engine, query: str) -> str:
    """
    Run single query and return response.
    Auto-activates a matching skill if found.
    
    Args:
        engine: Configured engine instance
        query: User query string
        
    Returns:
        Assistant response
    """
    # Auto-activate skill if query matches known keywords
    ctx = engine.skill_context
    if ctx and not ctx.is_skill_active:
        query_lower = query.lower()
        for summary in ctx.registry.get_all_summaries().values():
            query_match = any(
                kw in query_lower
                for kw in ["推荐", "广告", "recommendation", "ctr", "dcn", "deepfm", "research"]
            )
            if query_match:
                logger.info(f"[SingleQuery] Auto-activating skill '{summary.name}' for query")
                ctx.activate_skill(summary.name, goal=query[:200])
                print(f"🎯 Auto-activated skill: {summary.name}")
                break
    
    print(f"\n👤 User: {query}")
    print("\n🤖 Assistant: ", end="", flush=True)
    
    response = engine.chat(query)
    print(response)
    
    return response


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Agent X1 - LLM Agent System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                          # Interactive mode
  python main.py --query "Hello"          # Single query
  python main.py --provider anthropic     # Use Anthropic API
  python main.py --init-config            # Create default config
        """
    )
    
    # Config
    parser.add_argument(
        '--config', '-c',
        type=str,
        default=None,
        help='Path to config file (YAML or JSON)'
    )
    
    # Provider and API
    parser.add_argument(
        '--provider', '-p',
        type=str,
        choices=['kimi', 'anthropic', 'openai'],
        default=None,
        help='LLM provider'
    )
    
    parser.add_argument(
        '--api-key',
        type=str,
        default=None,
        help='API key (overrides config and env)'
    )
    
    parser.add_argument(
        '--model', '-m',
        type=str,
        default=None,
        help='Model identifier'
    )
    
    # Execution
    parser.add_argument(
        '--query', '-q',
        type=str,
        default=None,
        help='Single query mode'
    )
    
    # Utilities
    parser.add_argument(
        '--init-config',
        action='store_true',
        help='Create default config file and exit'
    )
    
    parser.add_argument(
        '--log-level',
        type=str,
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='Logging level'
    )
    
    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_arguments()
    
    # Initialize config if requested
    if args.init_config:
        config_path = "config/config.yaml"
        create_default_config_file(config_path)
        print(f"✅ Default config created at: {config_path}")
        print("Please edit with your API key before running.")
        return 0
    
    try:
        # Load configuration
        config = load_config(config_file=args.config)
        
        # Override with command line args
        if args.provider:
            provider_changed = config.llm.provider != args.provider
            config.llm.provider = args.provider
            # Reset base_url when provider changes to use provider-specific default
            if provider_changed:
                config.llm.base_url = ""
                # Also reset model if not explicitly provided
                if not args.model:
                    config.llm.model = ""
        if args.api_key:
            config.llm.api_key = args.api_key
        if args.model:
            config.llm.model = args.model
        if args.log_level:
            config.log_level = args.log_level
        
        # Setup logging
        import logging
        setup_logging(
            level=getattr(logging, args.log_level.upper()),
            log_dir=config.paths.log_dir
        )
        
        # Validate
        config.validate()
        
        logger.info(f"Starting Agent X1")
        logger.info(f"Provider: {config.llm.provider}")
        logger.info(f"Model: {config.llm.model}")
        
        # Initialize session manager
        session_manager = init_session_manager()
        session_dir = session_manager.get_session_directory()
        logger.info(f"Session directory: {session_dir}")
        print(f"📁 Session: {session_dir.name}")
        
        # Create engine
        engine = create_and_configure_engine(config)
        
        # Wire session directory into skill context
        if engine.skill_context:
            engine.skill_context.set_session_dir(str(session_dir))
        
        # Run mode
        if args.query:
            run_single_query(engine, args.query)
        else:
            run_interactive_mode(engine)
        
        return 0
        
    except FileNotFoundError as e:
        logger.error(f"Config file not found: {e}")
        print(f"\nConfig file not found: {e}")
        print("💡 Tip: Run with --init-config to create default config")
        return 1
        
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        print(f"\n Configuration error: {e}")
        return 1
        
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        print(f"\n Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
