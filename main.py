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
from pathlib import Path
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
from src.core.tool import configure_tool_defaults
from src.tools.codebase_search_tools import configure_subprocess_timeout

# New redesign imports
from src.session import SessionManager, get_default_manager as get_new_session_manager
from src.memory import MemoryController, MemoryStore
from src.prompt import PromptProvider
from src.context import ContextAssembler
from src.runtime import AgentLoop, ToolScheduler, LoopDetector, AgentConfig
from src.core.events import EventBus, get_event_bus

# New logging/display system imports
from src.util.display import ConsoleDisplay
from src.util.activity_stream import ActivityStream
from src.util.structured_log import StructuredLogger
from src.util.token_tracker import TokenTracker
from src.util.log_integration import LogIntegration

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


def create_agent_loop(engine, config: AppConfig, use_new_system: bool = False,
                      verbose: bool = False, debug: bool = False,
                      session_dir: Optional[str] = None,
                      continue_session: bool = False,
                      resume_session_id: Optional[str] = None) -> Optional[AgentLoop]:
    """
    Create AgentLoop with all components for the new architecture.
    
    Args:
        engine: Configured engine instance
        config: Application configuration
        use_new_system: If True, create AgentLoop; otherwise return None
        verbose: Enable verbose display output
        debug: Enable debug display output
        
    Returns:
        AgentLoop instance if use_new_system is True, otherwise None
    """
    if not use_new_system:
        return None
    
    try:
        # Initialize EventBus
        event_bus = get_event_bus()
        
        # Initialize new session manager
        new_session_manager = get_new_session_manager(config)
        new_session_manager.event_bus = event_bus
        
        # Session recovery or creation
        if continue_session:
            try:
                session = new_session_manager.continue_session()
                print(f"\n🔄 Resumed session {session.id[:8]} ({session.turn_count} turns, {session.budget.used} tokens used)")
            except ValueError as e:
                print(f"\n⚠️  {e}")
                print("   Creating new session instead.")
                session = new_session_manager.create_session(
                    name="Agent X1 Session",
                    working_dir=os.getcwd(),
                    session_dir=session_dir,
                )
                new_session_manager.activate_session(session.id)
        elif resume_session_id:
            try:
                session = new_session_manager.resume_session_by_id(resume_session_id)
                print(f"\n🔄 Resumed session {session.id[:8]} ({session.turn_count} turns, {session.budget.used} tokens used)")
            except ValueError as e:
                print(f"\n❌ {e}")
                return None
        else:
            # Create a new session (reuse legacy session dir if provided)
            session = new_session_manager.create_session(
                name="Agent X1 Session",
                working_dir=os.getcwd(),
                session_dir=session_dir,
            )
            new_session_manager.activate_session(session.id)
        
        # Initialize memory system
        memory_store = MemoryStore(str(Path(config.paths.data_dir) / "agent_x1.db"))
        memory_controller = MemoryController(memory_store)
        
        # Initialize prompt provider
        prompt_provider = PromptProvider()
        
        # Initialize context assembler
        context_assembler = ContextAssembler(
            session_manager=new_session_manager,
            memory_controller=memory_controller,
            prompt_provider=prompt_provider,
            context_config=config.context,
        )
        
        # Inject archive instance for recall_compressed_messages tool
        from src.tools.context_tools import set_archive_instance
        set_archive_instance(context_assembler._archive)
        
        # Initialize runtime components
        from src.core.tool import ToolRegistry
        tool_registry = ToolRegistry()
        for tool in engine.tools.values():
            tool_registry.register(tool)
        
        tool_scheduler = ToolScheduler(tool_registry, max_parallel=5, event_bus=event_bus)
        loop_detector = LoopDetector(
            window_size=6,
            threshold=0.85,
            max_repetitions=3
        )
        
        agent_config = AgentConfig(
            max_iterations=config.llm.max_iterations,
            max_parallel_tools=5,
            default_tool_timeout=config.tool_safety.default_timeout,
        )
        
        # Initialize logging/display chain
        display = ConsoleDisplay(verbose=verbose, debug=debug)
        activity_stream = ActivityStream(display=display, verbose=verbose or debug)
        token_tracker = TokenTracker()
        
        # Mirror activity stream output to a markdown file in session dir
        if session.session_dir:
            activity_log_path = str(Path(session.session_dir) / "session_activity.md")
            display.set_log_file(activity_log_path)
        
        # Initialize structured logger (writes to session directory)
        structured_logger = None
        if session.session_dir:
            structured_logger = StructuredLogger(
                session_dir=session.session_dir,
                session_id=session.id,
            )
        
        # Wire up EventBus log integration
        log_integration = LogIntegration(
            display=display,
            activity_stream=activity_stream,
            structured_logger=structured_logger,
            token_tracker=token_tracker,
        )
        log_integration.setup(event_bus)
        
        # Show log file locations to user
        display.blank_line()
        display.info("Activity Stream: real-time output below")
        if structured_logger and session.session_dir:
            display.info(f"Session logs:     {session.session_dir}/session_log.jsonl")
            display.info(f"Session activity: {session.session_dir}/session_activity.md")
            display.info(f"Session summary:  {session.session_dir}/session_summary.md")
        display.blank_line()
        
        # Create AgentLoop
        agent_loop = AgentLoop(
            engine=engine,
            session_manager=new_session_manager,
            context_assembler=context_assembler,
            tool_scheduler=tool_scheduler,
            loop_detector=loop_detector,
            config=agent_config,
            event_bus=event_bus,
            display=display,
            activity_stream=activity_stream,
        )
        
        logger.info("[AgentLoop] New architecture initialized successfully")
        return agent_loop
        
    except Exception as e:
        logger.warning(f"[AgentLoop] Failed to initialize new architecture: {e}")
        return None


def run_interactive_mode(engine, agent_loop: Optional[AgentLoop] = None) -> None:
    """
    Run interactive chat mode.
    
    Args:
        engine: Configured engine instance
        agent_loop: Optional AgentLoop for new architecture
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
                if agent_loop:
                    # New architecture doesn't need explicit clear
                    pass
                print("\n🧹 Conversation history cleared.")
                continue
            
            if cmd == '/history':
                if agent_loop:
                    # Use new session manager
                    from src.session import SessionManager
                    sm = agent_loop.session_manager
                    if sm.active_session:
                        turns = sm.get_history(recent_n=10)
                        print(f"\n📜 History ({len(turns)} turns):")
                        for i, turn in enumerate(turns, 1):
                            preview = turn.content[:50] + "..." if len(turn.content) > 50 else turn.content
                            print(f"  {i}. [{turn.role}] {preview}")
                else:
                    history = engine.get_conversation_history()
                    print(f"\n📜 History ({len(history)} messages):")
                    for i, msg in enumerate(history[-10:], 1):
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
            if agent_loop:
                response = agent_loop.run_sync(user_input)
            else:
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


def run_single_query(engine, query: str, agent_loop: Optional[AgentLoop] = None) -> str:
    """
    Run single query and return response.
    Auto-activates a matching skill if found.
    
    Args:
        engine: Configured engine instance
        query: User query string
        agent_loop: Optional AgentLoop for new architecture
        
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
    
    if agent_loop:
        response = agent_loop.run_sync(query)
    else:
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
    
    # New architecture flag
    parser.add_argument(
        '--new-arch',
        action='store_true',
        default=True,
        help='Use the new AgentLoop architecture (experimental)'
    )
    
    # Session recovery
    parser.add_argument(
        '--continue', '-C',
        action='store_true',
        dest='continue_session',
        default=False,
        help='Resume the most recent paused or active session'
    )
    
    parser.add_argument(
        '--resume',
        type=str,
        default=None,
        metavar='SESSION_ID',
        help='Resume a specific session by ID'
    )
    
    # Logging/display options
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        default=False,
        help='Enable verbose output (show more details in activity stream)'
    )
    
    parser.add_argument(
        '--debug',
        action='store_true',
        default=False,
        help='Enable debug output (implies --verbose, shows debug-level info)'
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
        
        # Apply tool safety configuration from config file
        configure_tool_defaults(
            default_timeout=config.tool_safety.default_timeout,
            default_max_output=config.tool_safety.default_max_output,
        )
        configure_subprocess_timeout(config.tool_safety.subprocess_timeout)
        
        # Initialize session manager
        session_manager = init_session_manager()
        session_dir = session_manager.get_session_directory()
        logger.info(f"Session directory: {session_dir}")
        print(f"\n📁 Session directory: {session_dir}")
        print(f"   └── session_llm.md  (LLM interaction log)")
        
        # Create engine
        engine = create_and_configure_engine(config)
        
        # Wire session directory into skill context
        if engine.skill_context:
            engine.skill_context.set_session_dir(str(session_dir))
        
        # Create AgentLoop for new architecture (if enabled)
        agent_loop = None
        if args.new_arch:
            print("\n🔧 Using new AgentLoop architecture (experimental)")
            agent_loop = create_agent_loop(
                engine, config,
                use_new_system=True,
                verbose=args.verbose,
                debug=args.debug,
                session_dir=str(session_dir) if session_dir else None,
                continue_session=args.continue_session,
                resume_session_id=args.resume,
            )
        
        # Run mode
        if args.query:
            run_single_query(engine, args.query, agent_loop)
        else:
            run_interactive_mode(engine, agent_loop)
        
        # Finalize new-architecture session (generate summary, flush logs)
        if agent_loop:
            try:
                sm = agent_loop.session_manager
                if sm.active_session:
                    logger.info("[Session] New-arch session completing and finalizing")
                    sm.complete_session()
            except Exception as e:
                logger.warning(f"[Session] Failed to finalize new-arch session: {e}")
            # Deactivate legacy session manager to prevent it from writing
            # incorrect summary (it has no LLM call records in new-arch mode)
            if session_manager and session_manager.session_active:
                session_manager.session_active = False
        
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
