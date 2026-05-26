import pandas as pd
import json
from datetime import datetime, timedelta

# ─── Config thresholds ───────────────────────────────────────────────────────
IDLE_CPU_THRESHOLD      = 5.0    # % — EC2/RDS avg CPU below this = idle
IDLE_DAYS_THRESHOLD     = 7      # days running with low CPU
EBS_UNATTACHED_DAYS     = 5      # days unattached before flagging
S3_COLD_DAYS            = 60     # days since last access = cold storage
EIP_FLAG_ALL            = True   # all unassociated EIPs are waste
RIGHTSIZE_CPU_THRESHOLD = 20.0   # % — EC2 below this = oversized

# Rightsizing map: current → recommended (one tier down)
RIGHTSIZE_MAP = {
    "t3.medium":   ("t3.small",    0.0208),
    "t3.large":    ("t3.medium",   0.0416),
    "m5.xlarge":   ("m5.large",    0.096),
    "m5.2xlarge":  ("m5.xlarge",   0.192),
    "m5.4xlarge":  ("m5.2xlarge",  0.384),
    "c5.4xlarge":  ("c5.2xlarge",  0.34),
    "c5.9xlarge":  ("c5.4xlarge",  0.68),
    "r5.2xlarge":  ("r5.xlarge",   0.252),
    "db.t3.medium":("db.t3.small", 0.034),
    "db.m5.large": ("db.t3.medium",0.068),
    "db.m5.xlarge":("db.m5.large", 0.171),
    "db.r5.2xlarge":("db.r5.xlarge",0.48),
}

# ─── Load data ────────────────────────────────────────────────────────────────
def load_data(filepath="aws_cost_data.csv"):
    df = pd.read_csv(filepath, parse_dates=["last_accessed"])
    df["days_since_access"] = (datetime.today() - df["last_accessed"]).dt.days
    return df

# ─── Detection rules ──────────────────────────────────────────────────────────

def detect_idle_ec2(df):
    findings = []
    ec2 = df[df["service"] == "EC2"].copy()
    idle = ec2[
        (ec2["cpu_avg_7d"] < IDLE_CPU_THRESHOLD) &
        (ec2["days_running"] >= IDLE_DAYS_THRESHOLD)
    ]
    for _, r in idle.iterrows():
        findings.append({
            "finding_id":       f"IDLE-EC2-{r['resource_id'][-6:]}",
            "category":         "Idle Resource",
            "severity":         "HIGH" if r["monthly_cost_usd"] > 100 else "MEDIUM",
            "service":          "EC2",
            "resource_id":      r["resource_id"],
            "resource_name":    r["resource_name"],
            "region":           r["region"],
            "team":             r["team"],
            "environment":      r["environment"],
            "detail":           f"Instance running {r['days_running']}d with avg CPU {r['cpu_avg_7d']}% — well below {IDLE_CPU_THRESHOLD}% threshold",
            "monthly_waste_usd": float(r["monthly_cost_usd"]),
            "recommendation":   f"Stop or terminate {r['resource_id']}. If needed occasionally, convert to spot or use auto-start/stop scheduler.",
            "cli_fix":          f"aws ec2 stop-instances --instance-ids {r['resource_id']} --region {r['region']}"
        })
    return findings


def detect_unattached_ebs(df):
    findings = []
    ebs = df[
        (df["service"] == "EBS") &
        (df["status"].str.contains("unattached", case=False))
    ]
    for _, r in ebs.iterrows():
        findings.append({
            "finding_id":       f"EBS-UNATTACHED-{r['resource_id'][-6:]}",
            "category":         "Zombie Resource",
            "severity":         "HIGH" if r["monthly_cost_usd"] > 50 else "MEDIUM",
            "service":          "EBS",
            "resource_id":      r["resource_id"],
            "resource_name":    r["resource_name"],
            "region":           r["region"],
            "team":             r["team"],
            "environment":      r["environment"],
            "detail":           f"Volume unattached for {r['days_running']} days, accruing ${r['monthly_cost_usd']}/mo with zero utilization",
            "monthly_waste_usd": float(r["monthly_cost_usd"]),
            "recommendation":   "Snapshot then delete if data not needed. If needed, attach to an instance.",
            "cli_fix":          f"aws ec2 create-snapshot --volume-id {r['resource_id']} --description 'backup-before-delete' && aws ec2 delete-volume --volume-id {r['resource_id']}"
        })
    return findings


def detect_unassociated_eips(df):
    findings = []
    eips = df[df["service"] == "Elastic IP"]
    for _, r in eips.iterrows():
        findings.append({
            "finding_id":       f"EIP-UNUSED-{r['resource_id'][-6:]}",
            "category":         "Zombie Resource",
            "severity":         "LOW",
            "service":          "Elastic IP",
            "resource_id":      r["resource_id"],
            "resource_name":    r["resource_name"],
            "region":           r["region"],
            "team":             r["team"],
            "environment":      r["environment"],
            "detail":           f"Elastic IP unassociated for {r['days_running']} days. AWS charges $3.60/mo per idle EIP.",
            "monthly_waste_usd": float(r["monthly_cost_usd"]),
            "recommendation":   "Release EIP if no longer needed.",
            "cli_fix":          f"aws ec2 release-address --allocation-id {r['resource_id']} --region {r['region']}"
        })
    return findings


def detect_cold_s3(df):
    findings = []
    s3 = df[
        (df["service"] == "S3") &
        (df["days_since_access"] >= S3_COLD_DAYS)
    ]
    for _, r in s3.iterrows():
        saving = round(r["monthly_cost_usd"] * 0.55, 2)  # Glacier ~55% cheaper
        findings.append({
            "finding_id":       f"S3-COLD-{r['resource_id'][-8:]}",
            "category":         "Storage Optimisation",
            "severity":         "MEDIUM",
            "service":          "S3",
            "resource_id":      r["resource_id"],
            "resource_name":    r["resource_name"],
            "region":           r["region"],
            "team":             r["team"],
            "environment":      r["environment"],
            "detail":           f"Bucket not accessed in {r['days_since_access']} days but on S3 Standard pricing. Move to Glacier.",
            "monthly_waste_usd": saving,
            "recommendation":   "Apply S3 Intelligent-Tiering or Lifecycle rule to move to Glacier after 30 days.",
            "cli_fix":          f"aws s3api put-bucket-lifecycle-configuration --bucket {r['resource_id']} --lifecycle-configuration file://glacier-lifecycle.json"
        })
    return findings


def detect_rightsizing(df):
    findings = []
    ec2_rds = df[df["service"].isin(["EC2", "RDS"])].copy()
    oversized = ec2_rds[
        (ec2_rds["cpu_avg_7d"] < RIGHTSIZE_CPU_THRESHOLD) &
        (ec2_rds["cpu_avg_7d"] >= IDLE_CPU_THRESHOLD)  # not idle, just oversized
    ]
    for _, r in oversized.iterrows():
        itype = r["resource_type"]
        if itype not in RIGHTSIZE_MAP:
            continue
        recommended, new_hourly = RIGHTSIZE_MAP[itype]
        saving = round(r["monthly_cost_usd"] - (new_hourly * 24 * 30), 2)
        if saving <= 0:
            continue
        findings.append({
            "finding_id":       f"RIGHTSIZE-{r['resource_id'][-6:]}",
            "category":         "Rightsizing",
            "severity":         "MEDIUM",
            "service":          r["service"],
            "resource_id":      r["resource_id"],
            "resource_name":    r["resource_name"],
            "region":           r["region"],
            "team":             r["team"],
            "environment":      r["environment"],
            "detail":           f"{itype} running at {r['cpu_avg_7d']}% avg CPU. Rightsizing to {recommended} saves ${saving}/mo.",
            "monthly_waste_usd": saving,
            "recommendation":   f"Downsize from {itype} to {recommended}. Schedule during maintenance window.",
            "cli_fix":          f"aws ec2 modify-instance-attribute --instance-id {r['resource_id']} --instance-type {{Value={recommended}}}"
        })
    return findings


def detect_idle_rds(df):
    findings = []
    rds = df[
        (df["service"] == "RDS") &
        (df["cpu_avg_7d"] < IDLE_CPU_THRESHOLD) &
        (df["days_running"] >= IDLE_DAYS_THRESHOLD)
    ]
    for _, r in rds.iterrows():
        findings.append({
            "finding_id":       f"IDLE-RDS-{r['resource_id'][-6:]}",
            "category":         "Idle Resource",
            "severity":         "HIGH",
            "service":          "RDS",
            "resource_id":      r["resource_id"],
            "resource_name":    r["resource_name"],
            "region":           r["region"],
            "team":             r["team"],
            "environment":      r["environment"],
            "detail":           f"RDS instance idle for {r['days_running']} days with {r['cpu_avg_7d']}% CPU. RDS cannot be stopped for more than 7 days without auto-restart.",
            "monthly_waste_usd": float(r["monthly_cost_usd"]),
            "recommendation":   "Take final snapshot and delete if unused. For dev/test, use Aurora Serverless v2 which scales to zero.",
            "cli_fix":          f"aws rds create-db-snapshot --db-instance-identifier {r['resource_id']} --db-snapshot-identifier {r['resource_id']}-final-snap"
        })
    return findings


# ─── Scoring & ranking ────────────────────────────────────────────────────────

SEVERITY_MULTIPLIER = {"HIGH": 1.5, "MEDIUM": 1.0, "LOW": 0.6}

def score_and_rank(findings, top_n=10):
    for f in findings:
        f["waste_score"] = round(
            f["monthly_waste_usd"] * SEVERITY_MULTIPLIER[f["severity"]], 2
        )
    ranked = sorted(findings, key=lambda x: x["waste_score"], reverse=True)
    for i, f in enumerate(ranked):
        f["rank"] = i + 1
    return ranked[:top_n]


# ─── Summary ──────────────────────────────────────────────────────────────────

def build_summary(all_findings, top10):
    total_waste = sum(f["monthly_waste_usd"] for f in all_findings)
    by_category = {}
    for f in all_findings:
        by_category.setdefault(f["category"], 0)
        by_category[f["category"]] += f["monthly_waste_usd"]

    return {
        "generated_at":         datetime.today().strftime("%Y-%m-%d %H:%M"),
        "total_findings":       len(all_findings),
        "total_monthly_waste":  round(total_waste, 2),
        "total_annual_waste":   round(total_waste * 12, 2),
        "waste_by_category":    {k: round(v, 2) for k, v in sorted(by_category.items(), key=lambda x: -x[1])},
        "top10_monthly_waste":  round(sum(f["monthly_waste_usd"] for f in top10), 2),
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def run_detection(filepath="aws_cost_data.csv"):
    print("Loading data...")
    df = load_data(filepath)

    print("Running detection rules...")
    all_findings = (
        detect_idle_ec2(df) +
        detect_idle_rds(df) +
        detect_unattached_ebs(df) +
        detect_unassociated_eips(df) +
        detect_cold_s3(df) +
        detect_rightsizing(df)
    )

    print(f"Total findings: {len(all_findings)}")

    top10 = score_and_rank(all_findings)
    summary = build_summary(all_findings, top10)

    output = {
        "summary": summary,
        "top_findings": top10,
        "all_findings": all_findings
    }

    with open("findings.json", "w") as f:
        json.dump(output, f, indent=2)

    print("\n" + "="*55)
    print(f"  CLOUD COST WASTE HUNTER — DETECTION RESULTS")
    print("="*55)
    print(f"  Total findings    : {summary['total_findings']}")
    print(f"  Monthly waste     : ${summary['total_monthly_waste']:,.2f}")
    print(f"  Annual waste      : ${summary['total_annual_waste']:,.2f}")
    print("="*55)
    print(f"\n  TOP 10 FINDINGS (ranked by waste score)\n")
    for f in top10:
        print(f"  #{f['rank']} [{f['severity']:6}] {f['category']:<22} ${f['monthly_waste_usd']:>8.2f}/mo  |  {f['resource_name']}")
    print("\n  Waste by category:")
    for cat, amt in summary["waste_by_category"].items():
        print(f"    {cat:<25} ${amt:,.2f}/mo")
    print("\n  findings.json written.")
    return output

if __name__ == "__main__":
    run_detection("aws_cost_data.csv")
