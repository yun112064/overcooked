"""Calculate a recipe JSON using TFDA first and optional LLM fallback."""
import argparse
import json
import os
from pathlib import Path
import shlex
import sys

from nutrition_service import calculate_recipe_nutrition


def load_local_env():
    """Load single-line LLM settings from cwd/.env without extra dependencies.

    Existing behavior is retained: file settings override process settings.
    Values support quotes, inline comments and an optional export prefix.
    Shell expansion and multiline values are deliberately unsupported.
    """
    try:
        lines = Path('.env').read_text(encoding='utf-8-sig').splitlines()
    except FileNotFoundError:
        return
    allowed = {'NUTRITION_LLM_PROVIDER', 'NUTRITION_LLM_ENDPOINT',
               'NUTRITION_LLM_MODEL', 'NUTRITION_LLM_API_KEY',
               'GEMINI_MODEL', 'GEMINI_API_KEY'}
    for line in lines:
        line = line.strip()
        if line.startswith('export '):
            line = line[7:].lstrip()
        name, separator, value = line.partition('=')
        name = name.strip()
        if separator and name in allowed:
            os.environ[name] = ' '.join(shlex.split(value, comments=True, posix=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='Recipe JSON path, or - for stdin')
    parser.add_argument('--output', help='Output JSON path; otherwise stdout')
    parser.add_argument('--preview-llm', action='store_true',
                        help='Show exact LLM input payloads without calling an API')
    args = parser.parse_args()
    try:
        load_local_env()
        raw = sys.stdin.read() if args.input == '-' else Path(args.input).read_text(encoding='utf-8-sig')
        recipe = json.loads(raw)
        if not isinstance(recipe, dict):
            raise ValueError('recipe must be an object')
        requests = []
        def collect(payload):
            requests.append(payload)
            return {'success': False, 'error': 'preview_only'}
        result = calculate_recipe_nutrition(recipe.get('recipe_name', ''), recipe.get('ingredients'),
            servings=recipe.get('servings', 1), cooked_weight_g=recipe.get('cooked_weight_g'),
            estimator=collect if args.preview_llm else None)
        if args.preview_llm:
            result = {'recipe_name': recipe.get('recipe_name', ''),
                      'request_count': len(requests), 'llm_requests': requests,
                      'input_valid': result['success']}
        output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output:
            Path(args.output).write_text(output + '\n', encoding='utf-8')
        else:
            print(output)
        if args.preview_llm:
            return 0 if result['input_valid'] else 2
        return 0 if result['success'] and result['nutrition_complete'] and result['cooking_adjustment_complete'] else 2
    except (ValueError, OSError, TypeError):
        print(json.dumps({'success': False, 'error': 'invalid_recipe_file_or_output'}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
