#!/usr/bin/env python3
"""Test M3 automation on Seth Riklin (project 796539)."""
import asyncio
import sys
sys.path.insert(0, '/Users/samlesueur/vero-power')

from m3_automation import process_m3
import requests
from dotenv import load_dotenv
load_dotenv()

PROJECT = {
    'id': 796539,
    'title': 'Seth Riklin',
    'status': 'ACTIVE',
    'address': ['11403 N Chestwood Dr, Houston, TX, 77024'],
    'custom': {
        'battery_model': 'Powerwall 3 (Tesla) 1707000-21-Y',
        'battery_kwh': 13.5,
    },
}

async def main():
    success = await process_m3(PROJECT)
    print(f'\nResult: {"SUCCESS" if success else "FAILED"}')

asyncio.run(main())
