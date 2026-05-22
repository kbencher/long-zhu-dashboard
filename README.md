# Long Zhu — Workstream Timeline

Streamlit dashboard that renders a Gantt-style workstream timeline for the
Long Zhu project. Reads tasks live from the Long Zhu Budget Google Sheet
so dates, owners, and durations can be edited there and reflected in the
chart on the next page load.

## Local dev

```
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Requires a `.streamlit/secrets.toml` with `gcp_service_account = { ... }`.

## Deploy

Hosted on [Streamlit Community Cloud](https://share.streamlit.io)
from this repo's `main` branch.
