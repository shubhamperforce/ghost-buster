import json
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import requests
from datetime import datetime

# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Cloud Cost Waste Hunter",
    page_icon="👻",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ─── Custom CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main-header {
        font-size: 2rem; font-weight: 700; color: #1a1a2e;
        display: flex; align-items: center; gap: 12px;
    }
    .sub-header {
        color: #666; font-size: 0.95rem; margin-top: -8px; margin-bottom: 24px;
    }
    .metric-card {
        background: white; border-radius: 12px; padding: 20px 24px;
        border: 1px solid #e8e8e8; box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    }
    .metric-label { font-size: 0.78rem; color: #888; font-weight: 500;
        text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
    .metric-value { font-size: 2rem; font-weight: 700; color: #1a1a2e; line-height: 1.1; }
    .metric-sub { font-size: 0.82rem; color: #e05252; margin-top: 4px; font-weight: 500; }
    .metric-good { color: #2e9e6b !important; }
    .exec-summary {
        background: #f0f7ff; border-left: 4px solid #3b82f6;
        border-radius: 0 8px 8px 0; padding: 16px 20px;
        font-size: 0.95rem; color: #1e3a5f; line-height: 1.6; margin-bottom: 24px;
    }
    .finding-card {
        background: white; border-radius: 10px; padding: 16px 20px;
        border: 1px solid #e8e8e8; margin-bottom: 12px;
        border-left: 4px solid #e05252;
    }
    .finding-card.medium { border-left-color: #f59e0b; }
    .finding-card.low { border-left-color: #6b7280; }
    .finding-rank { font-size: 0.75rem; color: #888; font-weight: 600; }
    .finding-name { font-size: 1rem; font-weight: 600; color: #1a1a2e; margin: 2px 0; }
    .finding-plain { font-size: 0.88rem; color: #444; line-height: 1.5; margin: 6px 0; }
    .finding-meta { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 8px; }
    .badge {
        font-size: 0.73rem; font-weight: 600; padding: 3px 10px;
        border-radius: 99px; display: inline-block;
    }
    .badge-high { background: #fee2e2; color: #b91c1c; }
    .badge-medium { background: #fef3c7; color: #92400e; }
    .badge-low { background: #f3f4f6; color: #374151; }
    .badge-team { background: #ede9fe; color: #5b21b6; }
    .badge-service { background: #e0f2fe; color: #075985; }
    .saving-tag { font-size: 0.88rem; font-weight: 700; color: #e05252; }
    .cli-box {
        background: #1e1e2e; color: #a6e3a1; font-family: monospace;
        font-size: 0.78rem; padding: 10px 14px; border-radius: 6px;
        margin-top: 8px; overflow-x: auto; white-space: nowrap;
    }
    .quick-win {
        background: #f0fdf4; border: 1px solid #bbf7d0;
        border-radius: 8px; padding: 12px 16px; margin-bottom: 8px;
        font-size: 0.88rem; color: #166534;
    }
    .slack-sent {
        background: #f0fdf4; border: 1px solid #bbf7d0;
        border-radius: 8px; padding: 12px 16px;
        font-size: 0.88rem; color: #166534; margin-top: 8px;
    }
    div[data-testid="stMetric"] { background: white; border-radius: 10px;
        padding: 16px; border: 1px solid #e8e8e8; }
</style>
""", unsafe_allow_html=True)

# ─── Load report ─────────────────────────────────────────────────────────────
@st.cache_data
def load_report(path="llm_report.json"):
    with open(path) as f:
        return json.load(f)

report = load_report()
findings    = report["findings"]
all_f       = report.get("all_findings", [])
team_data   = report.get("team_breakdown", {})
quick_wins  = report.get("quick_wins", [])

# ─── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 👻 Ghost Busters")
    st.markdown("*Cloud Cost Waste Hunter*")
    st.markdown("---")

    all_teams = sorted(set(f["team"] for f in findings))
    selected_teams = st.multiselect(
        "Filter by team", all_teams, default=all_teams
    )

    all_severities = ["HIGH", "MEDIUM", "LOW"]
    selected_sev = st.multiselect(
        "Filter by severity", all_severities, default=all_severities
    )

    st.markdown("---")
    st.markdown("**Slack webhook alert**")
    slack_url = st.text_input("Webhook URL", placeholder="https://hooks.slack.com/...")

    if st.button("🔔 Fire top finding alert", use_container_width=True):
        if slack_url and findings:
            top = findings[0]
            payload = {
                "blocks": [
                    {"type": "header", "text": {"type": "plain_text",
                        "text": "👻 Cloud Cost Waste Hunter Alert"}},
                    {"type": "section", "text": {"type": "mrkdwn",
                        "text": f"*#{top['rank']} — {top['resource_name']}*\n{top['plain_english']}"}},
                    {"type": "section", "fields": [
                        {"type": "mrkdwn", "text": f"*Monthly saving*\n${top['monthly_saving']:,.2f}"},
                        {"type": "mrkdwn", "text": f"*Team*\n{top['team']}"},
                        {"type": "mrkdwn", "text": f"*Action*\n{top['priority_action'][:80]}..."}
                    ]},
                    {"type": "divider"},
                    {"type": "section", "text": {"type": "mrkdwn",
                        "text": f"*Total waste across environment:* ${report['total_monthly_waste']:,.2f}/mo (${report['total_annual_waste']:,.2f}/yr)"}}
                ]
            }
            try:
                r = requests.post(slack_url, json=payload, timeout=5)
                if r.status_code == 200:
                    st.success("✅ Alert sent!")
                else:
                    st.error(f"Failed: {r.status_code}")
            except Exception as e:
                st.error(f"Error: {e}")
        else:
            st.warning("Enter a Slack webhook URL first")

    st.markdown("---")
    st.caption(f"Report generated: {report.get('generated_at','—')}")

# ─── Header ───────────────────────────────────────────────────────────────────
st.markdown('<div class="main-header">👻 Cloud Cost Waste Hunter</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">AI-powered AWS infrastructure waste detection · Perforce Global Jam 2026</div>', unsafe_allow_html=True)

# ─── Metric cards ─────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(f"""<div class="metric-card">
        <div class="metric-label">Monthly waste</div>
        <div class="metric-value">${report['total_monthly_waste']:,.0f}</div>
        <div class="metric-sub">recoverable this month</div>
    </div>""", unsafe_allow_html=True)
with c2:
    st.markdown(f"""<div class="metric-card">
        <div class="metric-label">Annual waste</div>
        <div class="metric-value">${report['total_annual_waste']:,.0f}</div>
        <div class="metric-sub">if left unaddressed</div>
    </div>""", unsafe_allow_html=True)
with c3:
    st.markdown(f"""<div class="metric-card">
        <div class="metric-label">Total findings</div>
        <div class="metric-value">{len(all_f)}</div>
        <div class="metric-sub">across {len(team_data)} teams</div>
    </div>""", unsafe_allow_html=True)
with c4:
    top_team = max(team_data, key=lambda t: team_data[t]["monthly_waste"]) if team_data else "—"
    top_waste = team_data[top_team]["monthly_waste"] if team_data else 0
    st.markdown(f"""<div class="metric-card">
        <div class="metric-label">Top offending team</div>
        <div class="metric-value">{top_team}</div>
        <div class="metric-sub">${top_waste:,.0f}/mo wasted</div>
    </div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ─── Executive summary ────────────────────────────────────────────────────────
st.markdown(f'<div class="exec-summary">🤖 <strong>AI Summary</strong><br>{report["executive_summary"]}</div>',
    unsafe_allow_html=True)

# ─── Charts row ───────────────────────────────────────────────────────────────
col_l, col_r = st.columns(2)

with col_l:
    st.markdown("#### Waste by team")
    if team_data:
        team_df = pd.DataFrame([
            {"Team": t, "Monthly Waste ($)": v["monthly_waste"], "Top Issue": v["top_issue"]}
            for t, v in sorted(team_data.items(), key=lambda x: -x[1]["monthly_waste"])
        ])
        fig = px.bar(team_df, x="Monthly Waste ($)", y="Team", orientation="h",
            color="Monthly Waste ($)", color_continuous_scale=["#fde8e8","#e05252"],
            text="Monthly Waste ($)", hover_data=["Top Issue"])
        fig.update_traces(texttemplate="$%{text:,.0f}", textposition="outside")
        fig.update_layout(showlegend=False, coloraxis_showscale=False,
            plot_bgcolor="white", paper_bgcolor="white",
            margin=dict(l=0, r=60, t=10, b=0), height=280,
            yaxis=dict(showgrid=False), xaxis=dict(showgrid=True, gridcolor="#f0f0f0"))
        st.plotly_chart(fig, use_container_width=True)

with col_r:
    st.markdown("#### Waste by category")
    if all_f:
        cat_totals = {}
        for f in all_f:
            cat_totals[f["category"]] = cat_totals.get(f["category"], 0) + f["monthly_waste_usd"]
        cat_df = pd.DataFrame([
            {"Category": k, "Monthly Waste ($)": round(v, 2)}
            for k, v in sorted(cat_totals.items(), key=lambda x: -x[1])
        ])
        colors = ["#e05252", "#f59e0b", "#3b82f6", "#8b5cf6", "#10b981"]
        fig2 = px.pie(cat_df, values="Monthly Waste ($)", names="Category",
            color_discrete_sequence=colors, hole=0.45)
        fig2.update_traces(textposition="outside", textinfo="label+percent")
        fig2.update_layout(showlegend=False, paper_bgcolor="white",
            margin=dict(l=0, r=0, t=10, b=0), height=280)
        st.plotly_chart(fig2, use_container_width=True)

# ─── Quick wins ───────────────────────────────────────────────────────────────
st.markdown("#### ⚡ Quick wins — do these today")
qcols = st.columns(3)
for i, (win, col) in enumerate(zip(quick_wins, qcols)):
    with col:
        st.markdown(f'<div class="quick-win">✅ {win}</div>', unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ─── Top findings ─────────────────────────────────────────────────────────────
st.markdown("#### 🔍 Top findings")

filtered = [
    f for f in findings
    if f["team"] in selected_teams
    and report["raw_top10"][f["rank"]-1]["severity"] in selected_sev
]

if not filtered:
    st.info("No findings match the current filters.")
else:
    show_cli = st.toggle("Show CLI remediation commands", value=False)

    for f in filtered:
        raw = report["raw_top10"][f["rank"]-1]
        sev  = raw.get("severity", "MEDIUM").lower()
        card_class = f"finding-card {sev}"

        cli_html = ""
        if show_cli:
            cli_html = f'<div class="cli-box">$ {f["cli_fix"]}</div>'

        st.markdown(f"""
        <div class="{card_class}">
            <div class="finding-rank">FINDING #{f['rank']}</div>
            <div class="finding-name">{f['resource_name']}</div>
            <div class="finding-plain">{f['plain_english']}</div>
            <div style="font-size:0.82rem;color:#666;margin:4px 0;">
                <em>Impact: {f['business_impact']}</em>
            </div>
            <div class="finding-meta">
                <span class="badge badge-{sev}">{sev.upper()}</span>
                <span class="badge badge-team">👤 {f['team']}</span>
                <span class="badge badge-service">☁️ {raw.get('service','')}</span>
                <span class="saving-tag">💰 ${f['monthly_saving']:,.2f}/mo saving</span>
            </div>
            <div style="font-size:0.82rem;color:#555;margin-top:8px;">
                🔧 {f['priority_action']}
            </div>
            {cli_html}
        </div>
        """, unsafe_allow_html=True)

# ─── Recommendation ───────────────────────────────────────────────────────────
st.markdown("---")
st.markdown("#### 📋 Leadership recommendation")
st.info(report.get("closing_recommendation", ""))
st.caption("Built for Perforce Global Jam 2026 · Team Ghost Busters · Cloud Cost Waste Hunter")
