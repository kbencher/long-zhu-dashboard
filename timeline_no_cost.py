"""Long Zhu — Workstream Timeline without dollar amounts.

Separate Streamlit Cloud app (main file: timeline_no_cost.py) for sharing
the schedule without budget figures.  Runs streamlit_app.py with costs off.
"""
import os
import runpy

runpy.run_path(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'streamlit_app.py'),
    init_globals={'HIDE_COSTS': True},
    run_name='__main__',
)
