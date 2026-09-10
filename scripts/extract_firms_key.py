#!/usr/bin/env python3
import json
from pathlib import Path

notebook = json.loads(Path('Kalimantan_FireWatch_End_to_End.ipynb').read_text(encoding='utf-8'))
for cell in notebook['cells']:
    for line in cell.get('source', []):
        if line.strip().startswith('FIRMS_MAP_KEY'):
            print(line.split('=', 1)[1].strip().strip(chr(34)).strip(chr(39)))
            raise SystemExit
raise RuntimeError('FIRMS_MAP_KEY not found')