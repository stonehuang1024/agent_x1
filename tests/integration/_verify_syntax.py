# DELETE THIS FILE - temporary artifact from skill framework development
import ast
import sys

files = [
    'src/skills/models.py',
    'src/skills/loader.py',
    'src/skills/registry.py',
    'src/skills/workspace.py',
    'src/skills/context_manager.py',
    'src/skills/__init__.py',
    'src/engine/base.py',
    'src/engine/anthropic_engine.py',
    'src/engine/kimi_engine.py',
    'src/__init__.py',
    'main.py',
]

failed = []
for f in files:
    try:
        with open(f) as fh:
            ast.parse(fh.read())
        print(f'OK: {f}')
    except SyntaxError as e:
        print(f'FAIL: {f} -> {e}')
        failed.append(f)
    except FileNotFoundError as e:
        print(f'MISSING: {f}')
        failed.append(f)

if failed:
    print(f'\nFAILED FILES: {failed}')
    sys.exit(1)
else:
    print('\nALL SYNTAX OK')
