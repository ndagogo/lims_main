"""Markers and helpers for client-demo seed data that can be wiped later.

All demo rows are tagged so `clear_demo` can remove them without touching real data.
"""
from __future__ import annotations

DEMO_PASSWORD = 'Demo-Client-Walkthrough-2026!'
DEMO_EMAIL_DOMAIN = 'demo.hfdn.local'
DEMO_TAG = '[DEMO SEED]'
DEMO_HEALTH_CARD = 'DEMO-SEED'
DEMO_CENTRE_CODES = ('DEM1', 'DEM2')
DEMO_USERNAMES = (
    'demo.admin',
    'demo.desk',
    'demo.accounts',
    'demo.lab',
    'demo.radio',
)
DEMO_TARIFF_NAMES = ('DEMO Standard', 'DEMO HMO')
DEMO_TEST_CODES = ('DEMO-CBC', 'DEMO-MP', 'DEMO-FBS', 'DEMO-LFT', 'DEMO-URINE')
DEMO_IMAGING_CODES = ('DEMO-XR-CHEST', 'DEMO-US-ABD', 'DEMO-ECG')
DEMO_DOCTOR_CODES = ('DEMO-DR01', 'DEMO-DR02', 'DEMO-DR03')
DEMO_SERVICE_GROUP_NAME = 'DEMO Haematology'
DEMO_SERVICE_GROUP_CODE = '8'
