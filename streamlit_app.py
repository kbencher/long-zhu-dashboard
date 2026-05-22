"""Long Zhu — Workstream Gantt dashboard.

Reads the budget Google Sheet, builds a filterable Gantt chart styled per
the client mockup (workstream-color bars, owner labels overlaid, today
marker, filter pills at top).
"""
from datetime import datetime
from dateutil.relativedelta import relativedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


# ── Page setup ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title='Long Zhu — Workstream Timeline',
    page_icon='📅',
    layout='wide',
)


# ── Workstream colours (match mockup) ───────────────────────────────────────
WORKSTREAM_COLORS = {
    'Game Development': '#2D6A3F',
    'Testing':          '#A03D2D',
    'Marketing':        '#1F5A8C',
    'Community':        '#5B47B0',
    'Sales & Ops':      '#6E7479',
}
FILTER_TO_WS = {
    'All':         None,
    'Game dev':    'Game Development',
    'Testing':     'Testing',
    'Marketing':   'Marketing',
    'Community':   'Community',
    'Sales & ops': 'Sales & Ops',
}


# ── Data ────────────────────────────────────────────────────────────────────
SHEET_KEY = '1rKFY6S-VZFnOkZLs_JeNtZSkFZIkyVbSROSrmx0rb40'


@st.cache_data(ttl=300)
def load_tasks() -> pd.DataFrame:
    """Pull tasks from the Long Zhu Budget Google Sheet and normalise into a
    flat DataFrame ready for plotting."""
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_info(
        dict(st.secrets['gcp_service_account']),
        scopes=['https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive'],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_KEY)
    rows = sh.sheet1.get('A1:I40', value_render_option='FORMATTED_VALUE')

    def _parse_date(s):
        if not s:
            return None
        for fmt in ('%m/%d/%y', '%m/%d/%Y', '%Y-%m-%d'):
            try:
                return datetime.strptime(str(s).strip(), fmt)
            except ValueError:
                pass
        return None

    section = 'Monthly'
    out = []
    for r in rows:
        if not r or len(r) < 8:
            continue
        if len(r) > 1 and 'One-Time' in str(r[1]):
            section = 'One-Time'
            continue
        active_raw = str(r[0]).strip().upper()
        if section == 'Monthly':
            active = True
        else:
            active = (active_raw == 'TRUE')
        if not active:
            continue
        start = _parse_date(r[7] if len(r) > 7 else '')
        if not start:
            continue
        col_i = str(r[8] or '').strip() if len(r) > 8 else ''
        end = _parse_date(col_i)
        if end is None:
            try:
                n = int(float(col_i.replace(',', '')))
                end = start + relativedelta(months=n)
            except (ValueError, AttributeError):
                continue
        ws_group = str(r[2] or section).strip()
        # Bucket into workstreams matching the mockup
        bucket = _bucket_workstream(ws_group, str(r[1] or '').strip())
        out.append({
            'workstream': bucket,
            'sub':        ws_group,
            'department': str(r[1] or '').strip(),
            'owner':      str(r[3] or 'TBD').strip() or 'TBD',
            'notes':      str(r[4] or '').strip() if len(r) > 4 else '',
            'start':      start,
            'end':        end,
        })
    return pd.DataFrame(out)


def _bucket_workstream(ws_group: str, dept: str) -> str:
    g = (ws_group or '').lower()
    d = (dept or '').lower()
    if 'game' in g or 'identity' in g or 'illustration' in g or 'design' in g \
            or 'story' in g or 'tournament' in g:
        return 'Game Development'
    if 'testing' in g:
        return 'Testing'
    if 'marketing' in g:
        return 'Marketing'
    if 'community' in g or 'community' in d:
        return 'Community'
    if 'sales' in g or 'distribution' in g or 'admin' in g or 'monthly' in g:
        return 'Sales & Ops'
    return 'Sales & Ops'


def _task_label(row) -> str:
    """2-line label: notes (bolded) over owner."""
    primary = row['notes'] if row['notes'] else row['department']
    if len(primary) > 38:
        primary = primary[:36] + '…'
    return f"<b>{primary}</b><br><span style='color:#888;font-size:11px'>{row['owner']}</span>"


# ── Build Plotly Gantt ──────────────────────────────────────────────────────
def render_gantt(df: pd.DataFrame, today: datetime):
    # Order: by workstream (in mockup order), then by start date
    ws_order = ['Game Development', 'Testing', 'Marketing', 'Community', 'Sales & Ops']
    df['_ws_rank'] = df['workstream'].map({w: i for i, w in enumerate(ws_order)})
    df = df.sort_values(['_ws_rank', 'start']).reset_index(drop=True)
    df['_label'] = df.apply(_task_label, axis=1)
    # Plotly's category axis dedupes identical labels into the same row, so
    # for visually-identical labels (e.g., two TBDs) we'd lose rows.  Make
    # each label unique by appending a zero-width identifier.
    df['_label'] = [f"{lbl}<span style='display:none'>{i}</span>"
                     for i, lbl in enumerate(df['_label'])]
    df['_color'] = df['workstream'].map(WORKSTREAM_COLORS)

    # Use px.timeline — purpose-built Gantt that handles date-typed axes
    import plotly.express as px
    fig = px.timeline(
        df,
        x_start='start',
        x_end='end',
        y='_label',
        color='workstream',
        color_discrete_map=WORKSTREAM_COLORS,
        custom_data=['owner', 'notes', 'department'],
    )

    # Owner label overlaid inside each bar
    fig.update_traces(
        text=df['owner'],
        textposition='inside',
        insidetextanchor='middle',
        textfont=dict(color='white', size=11, family='Inter'),
        hovertemplate=(
            '<b>%{customdata[1]}</b><br>'
            'Owner: %{customdata[0]}<br>'
            '%{base|%b %Y} → %{x|%b %Y}<extra></extra>'
        ),
    )

    # Reverse y so the first task is on top
    fig.update_yaxes(autorange='reversed')

    # Vertical "Today" marker
    fig.add_vline(
        x=today,
        line=dict(color='#e74c3c', width=2),
    )

    # X-axis: monthly ticks across the top
    x_min = df['start'].min() - relativedelta(days=10)
    x_max = df['end'].max() + relativedelta(days=10)
    fig.update_xaxes(
        type='date',
        range=[x_min, x_max],
        tickformat='%b %y',
        dtick='M1',
        side='top',
        showgrid=True,
        gridcolor='#eee',
        showline=False,
        tickfont=dict(size=11, color='#555'),
        showticklabels=True,
        ticks='outside',
        ticklen=4,
    )
    fig.update_yaxes(
        autorange='reversed',
        showgrid=False,
        tickfont=dict(size=12, color='#222'),
    )

    fig.update_layout(
        height=max(420, 32 * len(df) + 80),
        margin=dict(l=20, r=40, t=60, b=40),
        plot_bgcolor='white',
        paper_bgcolor='white',
        showlegend=False,
        bargap=0.30,
    )
    return fig


# ── UI ──────────────────────────────────────────────────────────────────────
st.markdown(
    "<h2 style='margin-bottom:0;'>Long Zhu — Workstream Timeline</h2>",
    unsafe_allow_html=True,
)
st.write('')

# Filter pills row
filter_choice = st.pills(
    'Filter:',
    list(FILTER_TO_WS.keys()),
    default='All',
    label_visibility='visible',
)
if filter_choice is None:
    filter_choice = 'All'

# Legend pills (visual reference, not interactive)
legend_html = (
    '<div style="display:flex; gap:18px; align-items:center; '
    'font-size:13px; color:#444; margin-top:6px; margin-bottom:14px;">'
)
for label, color in [
    ('Game development', WORKSTREAM_COLORS['Game Development']),
    ('Testing',          WORKSTREAM_COLORS['Testing']),
    ('Marketing',        WORKSTREAM_COLORS['Marketing']),
    ('Community',        WORKSTREAM_COLORS['Community']),
    ('Sales & ops',      WORKSTREAM_COLORS['Sales & Ops']),
]:
    legend_html += (
        f'<span style="display:inline-flex;align-items:center;gap:6px;">'
        f'<span style="display:inline-block;width:10px;height:10px;border-radius:2px;'
        f'background:{color};"></span>{label}</span>'
    )
legend_html += (
    '<span style="display:inline-flex;align-items:center;gap:6px;">'
    '<span style="display:inline-block;width:2px;height:14px;background:#e74c3c;"></span>'
    'Today</span></div>'
)
st.markdown(legend_html, unsafe_allow_html=True)

try:
    df = load_tasks()
except Exception as e:
    st.error(f'Could not load Long Zhu Budget sheet: {e}')
    st.stop()

# Apply filter
selected_ws = FILTER_TO_WS.get(filter_choice)
if selected_ws:
    df_view = df[df['workstream'] == selected_ws].copy()
else:
    df_view = df.copy()

if df_view.empty:
    st.warning(f'No tasks matched the "{filter_choice}" filter.')
    st.stop()

fig = render_gantt(df_view, today=datetime.now())
st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

st.caption(f'{len(df_view)} active task(s) shown · '
            f'Source: Long Zhu Budget Google Sheet (auto-refreshes every 5 min)')
