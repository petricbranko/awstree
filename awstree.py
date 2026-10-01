#!/usr/bin/env python3
"""awstree: see where your AWS money goes, as a disktree-style treemap.

    python3 awstree.py            # read your AWS bill (last 30 days) and open the map
    python3 awstree.py --demo     # fake data, no AWS needed

Uses boto3 if installed, otherwise the `aws` CLI. No other dependencies.
"""
import argparse
import datetime as dt
import json
import random
import re
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "viewer.html"
OUT = HERE / "out"  # everything generated goes here (git-ignored: it contains your billing data)

# Each view is one Cost Explorer query grouped by two dimensions: outer box -> inner box.
VIEWS = {
    "service": ["SERVICE", "USAGE_TYPE"],
    "account": ["LINKED_ACCOUNT", "SERVICE"],
    "region": ["REGION", "SERVICE"],
}

# Credits and refunds are negative and can't be drawn as boxes; they go in the bill panel instead.
NO_CREDITS = {"Not": {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit", "Refund"]}}}


def die(msg):
    print(f"\nawstree: {msg}", file=sys.stderr)
    sys.exit(1)


class CostExplorer:
    """Tiny wrapper: boto3 when available, `aws` CLI otherwise."""

    def __init__(self, profile):
        self.profile = profile
        self.requests = 0
        try:
            import boto3

            session = boto3.Session(profile_name=profile) if profile else boto3.Session()
            self.client = session.client("ce", region_name="us-east-1")
        except Exception as e:
            # No boto3, or credentials boto3 can't load (e.g. `aws login` without botocore[crt]): use the CLI.
            self.client = None
            if not shutil.which("aws"):
                die(f"need the AWS CLI or a working boto3 ({e}).")

    def call(self, **params):
        self.requests += 1
        if self.client:
            try:
                return self.client.get_cost_and_usage(**params)
            except Exception as e:  # botocore raises many types; the message is what matters
                die(explain(str(e)))
        cmd = ["aws", "ce", "get-cost-and-usage", "--region", "us-east-1", "--output", "json",
               "--cli-input-json", json.dumps(params)]
        if self.profile:
            cmd += ["--profile", self.profile]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            die(explain(r.stderr.strip()))
        return json.loads(r.stdout)

    def pages(self, **params):
        token = None
        while True:
            page = self.call(**params, **({"NextPageToken": token} if token else {}))
            yield page
            token = page.get("NextPageToken")
            if not token:
                return


def explain(err):
    hints = []
    if "AccessDenied" in err or "not authorized" in err:
        hints.append("Your credentials need the ce:GetCostAndUsage permission.")
    if "credentials" in err.lower() or "Unable to locate" in err or "expired" in err.lower():
        hints.append("Log in first, e.g. `aws sso login --profile <name>` or `aws configure`.")
    if "DataUnavailable" in err or "not enabled" in err.lower():
        hints.append("Enable Cost Explorer in the Billing console (takes up to 24h the first time).")
    return "AWS error: " + err + ("\n  -> " + "\n  -> ".join(hints) if hints else "")


def utc_today():
    return dt.datetime.now(dt.timezone.utc).date()


def fetch(profile, days, metric):
    end = utc_today()  # Cost Explorer days are UTC; End is exclusive, so this means "through yesterday"
    start = end - dt.timedelta(days=2 * days)  # current window + previous window, for the "vs prev" deltas
    dates = [(start + dt.timedelta(i)).isoformat() for i in range(2 * days)]
    index = {d: i for i, d in enumerate(dates)}
    ce = CostExplorer(profile)

    views = {}
    for name, dims in VIEWS.items():
        print(f"  reading {name} view ...", flush=True)
        rows, names = {}, {}
        for page in ce.pages(
            TimePeriod={"Start": dates[0], "End": end.isoformat()},
            Granularity="DAILY",
            Metrics=[metric],
            GroupBy=[{"Type": "DIMENSION", "Key": d} for d in dims],
            Filter=NO_CREDITS,
        ):
            for attr in page.get("DimensionValueAttributes", []):
                if attr.get("Attributes", {}).get("description"):
                    names[attr["Value"]] = attr["Attributes"]["description"]
            for day in page["ResultsByTime"]:
                i = index.get(day["TimePeriod"]["Start"])
                if i is None:
                    continue
                for g in day.get("Groups", []):
                    amount = float(g["Metrics"][metric]["Amount"])
                    if amount:
                        rows.setdefault(tuple(g["Keys"]), [0.0] * len(dates))[i] += amount
        views[name] = {"dims": dims, "names": names, "rows": pack(rows)}

    print("  reading bill totals ...", flush=True)
    bill = {}
    page = ce.call(
        TimePeriod={"Start": dates[days], "End": end.isoformat()},
        Granularity="MONTHLY",
        Metrics=[metric],
        GroupBy=[{"Type": "DIMENSION", "Key": "RECORD_TYPE"}],
    )
    for period in page["ResultsByTime"]:
        for g in period.get("Groups", []):
            bill[g["Keys"][0]] = bill.get(g["Keys"][0], 0) + float(g["Metrics"][metric]["Amount"])

    print(f"  done: {ce.requests} Cost Explorer requests (~${ce.requests * 0.01:.2f})")
    return {"days": dates, "split": days, "views": views, "bill": bill, "demo": False}


def pack(rows):
    out = [{"k": list(k), "d": [round(v, 4) for v in d]} for k, d in rows.items()]
    return [r for r in out if abs(sum(r["d"])) >= 0.005]


# ---------------------------------------------------------------- demo data

P, S, D, X = "111111111111", "222222222222", "333333333333", "444444444444"
DEMO_ACCOUNTS = {P: "acme-prod", S: "acme-staging", D: "acme-data", X: "sandbox-dev"}
REGION_PREFIX = {"us-east-1": "USE1", "us-west-2": "USW2", "eu-west-1": "EUW1", "global": ""}

# service, usage type, $/day, growth over the 60 days, [(account, region, share)]
DEMO = [
    ("Amazon Elastic Compute Cloud - Compute", "BoxUsage:m6i.2xlarge", 62, 0.05, [(P, "us-east-1", .7), (P, "eu-west-1", .3)]),
    ("Amazon Elastic Compute Cloud - Compute", "BoxUsage:c6i.4xlarge", 41, -0.35, [(P, "us-east-1", 1)]),
    ("Amazon Elastic Compute Cloud - Compute", "BoxUsage:m4.xlarge", 9, 0, [(S, "us-east-1", 1)]),
    ("Amazon Elastic Compute Cloud - Compute", "BoxUsage:t3.large", 7, 0, [(S, "us-east-1", .6), (X, "us-west-2", .4)]),
    ("Amazon Elastic Compute Cloud - Compute", "SpotUsage:g5.2xlarge", 18, 0.8, [(D, "us-west-2", 1)]),
    ("EC2 - Other", "EBS:VolumeUsage.gp3", 14, 0.05, [(P, "us-east-1", .6), (D, "us-west-2", .4)]),
    ("EC2 - Other", "EBS:VolumeUsage.gp2", 6, 0, [(S, "us-east-1", 1)]),
    ("EC2 - Other", "EBS:SnapshotUsage", 11, 0.2, [(P, "us-east-1", .8), (S, "us-east-1", .2)]),
    ("EC2 - Other", "NatGateway-Hours", 4.3, 0, [(P, "us-east-1", .5), (S, "us-east-1", .25), (D, "us-west-2", .25)]),
    ("EC2 - Other", "NatGateway-Bytes", 16, 0.6, [(P, "us-east-1", .7), (D, "us-west-2", .3)]),
    ("EC2 - Other", "DataTransfer-Regional-Bytes", 5, 0.1, [(P, "us-east-1", 1)]),
    ("Amazon Relational Database Service", "InstanceUsage:db.r6g.2xlarge", 38, 0, [(P, "us-east-1", 1)]),
    ("Amazon Relational Database Service", "Multi-AZUsage:db.r6g.large", 17, 0, [(P, "eu-west-1", 1)]),
    ("Amazon Relational Database Service", "RDS:GP3-Storage", 6, 0.03, [(P, "us-east-1", 1)]),
    ("Amazon Relational Database Service", "ExtendedSupport:Yr1-Yr2:PostgreSQL11", 4.8, 0, [(S, "us-east-1", 1)]),
    ("Amazon Relational Database Service", "Aurora:StorageIOUsage", 9, 0.25, [(D, "us-west-2", 1)]),
    ("Amazon Simple Storage Service", "TimedStorage-ByteHrs", 21, 0.04, [(D, "us-west-2", .7), (P, "us-east-1", .3)]),
    ("Amazon Simple Storage Service", "TimedStorage-INT-FA-ByteHrs", 5, 0, [(D, "us-west-2", 1)]),
    ("Amazon Simple Storage Service", "Requests-Tier1", 4, 0.1, [(P, "us-east-1", 1)]),
    ("Amazon Simple Storage Service", "Requests-Tier2", 2, 0, [(P, "us-east-1", 1)]),
    ("Amazon CloudFront", "US-DataTransfer-Out-Bytes", 13, 0.05, [(P, "global", 1)]),
    ("Amazon CloudFront", "EU-DataTransfer-Out-Bytes", 5, 0.05, [(P, "global", 1)]),
    ("Amazon CloudFront", "US-Requests-Tier2-HTTPS", 2.5, 0, [(P, "global", 1)]),
    ("AWS Lambda", "Lambda-GB-Second", 7, 0.1, [(P, "us-east-1", .6), (D, "us-west-2", .4)]),
    ("AWS Lambda", "Request", 1.1, 0, [(P, "us-east-1", 1)]),
    ("Amazon Elastic Kubernetes Service", "AmazonEKS-Hours:perCluster", 4.8, 0, [(P, "us-east-1", .5), (S, "us-east-1", .5)]),
    ("Amazon Elastic Kubernetes Service", "AmazonEKS-Hours:extendedSupport", 12, 0, [(S, "us-east-1", 1)]),
    ("Amazon Bedrock", "Model-InputTokenCount", 7, 3, [(P, "us-east-1", .7), (X, "us-west-2", .3)]),
    ("Amazon Bedrock", "Model-OutputTokenCount", 12, 3, [(P, "us-east-1", .7), (X, "us-west-2", .3)]),
    ("Amazon SageMaker", "Notebook:ml.g5.xlarge", 8, 0, [(D, "us-west-2", 1)]),
    ("Amazon CloudWatch", "DataProcessing-Bytes", 9, 0.4, [(P, "us-east-1", .8), (S, "us-east-1", .2)]),
    ("Amazon CloudWatch", "TimedStorage-ByteHrs", 3, 0.05, [(P, "us-east-1", 1)]),
    ("Amazon CloudWatch", "CW:MetricMonitorUsage", 3.5, 0, [(P, "us-east-1", 1)]),
    ("Amazon DynamoDB", "WriteCapacityUnit-Hrs", 5, 0, [(P, "us-east-1", 1)]),
    ("Amazon DynamoDB", "ReadCapacityUnit-Hrs", 2.5, 0, [(P, "us-east-1", 1)]),
    ("Amazon ElastiCache", "NodeUsage:cache.r6g.large", 7.2, 0, [(P, "us-east-1", 1)]),
    ("Amazon OpenSearch Service", "ESInstance:r6g.large.search", 10, 0, [(D, "us-west-2", 1)]),
    ("Amazon Virtual Private Cloud", "PublicIPv4:InUseAddress", 2.2, 0, [(P, "us-east-1", .7), (S, "us-east-1", .3)]),
    ("Amazon Virtual Private Cloud", "PublicIPv4:IdleAddress", 0.9, 0, [(X, "us-west-2", 1)]),
    ("Amazon Virtual Private Cloud", "VpcEndpoint-Hours", 1.5, 0, [(P, "us-east-1", 1)]),
    ("Amazon Elastic Load Balancing", "LoadBalancerUsage", 3.2, 0, [(P, "us-east-1", .6), (S, "us-east-1", .4)]),
    ("Amazon Elastic Load Balancing", "LCUUsage", 2.4, 0.1, [(P, "us-east-1", 1)]),
    ("Amazon Athena", "DataScannedInTB", 3, 0.3, [(D, "us-west-2", 1)]),
    ("AWS Glue", "ETL-DPU-Hour", 4.5, 0, [(D, "us-west-2", 1)]),
    ("Amazon Kinesis", "Storage-ShardHour", 2.8, 0, [(D, "us-west-2", 1)]),
    ("Amazon EC2 Container Registry (ECR)", "TimedStorage-ByteHrs", 0.8, 0.05, [(P, "us-east-1", 1)]),
    ("AWS Key Management Service", "KMS-Keys", 0.6, 0, [(P, "us-east-1", 1)]),
    ("AWS Secrets Manager", "AWSSecretsManager-Secrets", 0.5, 0, [(P, "us-east-1", 1)]),
    ("Amazon Route 53", "HostedZone", 0.4, 0, [(P, "global", 1)]),
    ("Amazon Route 53", "DNS-Queries", 0.3, 0, [(P, "global", 1)]),
    ("AWS Config", "ConfigurationItemRecorded", 1.4, 0, [(P, "us-east-1", .5), (S, "us-east-1", .5)]),
    ("Amazon GuardDuty", "PaidEventsAnalyzed", 1.2, 0, [(P, "us-east-1", 1)]),
    ("Amazon Simple Queue Service", "Requests-RBP", 0.4, 0, [(P, "us-east-1", 1)]),
]


def demo(days):
    rnd = random.Random(7)
    end = utc_today()
    n = 2 * days
    dates = [(end - dt.timedelta(n - i)).isoformat() for i in range(n)]
    weekend = [dt.date.fromisoformat(d).weekday() >= 5 for d in dates]
    views = {v: {} for v in VIEWS}
    for service, usage, per_day, growth, spots in DEMO:
        bursty = any(w in usage for w in ("Request", "Lambda", "Bytes", "Token", "DataScanned"))
        for account, region, share in spots:
            prefix = REGION_PREFIX[region]
            ut = f"{prefix}-{usage}" if prefix else usage
            daily = []
            for i in range(n):
                m = (1 + growth * i / (n - 1)) / (1 + growth / 2)  # keep the average near per_day
                m *= 0.8 if bursty and weekend[i] else 1
                daily.append(max(0.0, per_day * share * m * rnd.gauss(1, 0.06)))
            for view, key in (("service", (service, ut)), ("account", (account, service)), ("region", (region, service))):
                acc = views[view].setdefault(key, [0.0] * n)
                for i, v in enumerate(daily):
                    acc[i] += v
    usage_total = sum(sum(d[days:]) for d in views["service"].values())
    return {
        "days": dates,
        "split": days,
        "views": {v: {"dims": VIEWS[v], "names": DEMO_ACCOUNTS if v == "account" else {}, "rows": pack(rows)}
                  for v, rows in views.items()},
        "bill": {"Usage": usage_total, "Credit": -1500.0, "Tax": round(usage_total * 0.02, 2)},
        "demo": True,
    }


# ---------------------------------------------------------------- AI export

# Known money pits, matched against usage types. Shared with the viewer's "worth a look" panel,
# so the patterns must be valid in both Python and JavaScript.
PITS = [
    (r"IdleAddress", "idle public IPs — release them"),
    (r"NatGateway-Bytes", "NAT data processing — VPC endpoints for S3/ECR/DynamoDB are cheaper"),
    (r"ExtendedSupport|extendedSupport", "extended support fees — upgrade the version"),
    (r"SnapshotUsage", "EBS snapshots — check retention / old snapshots"),
    (r"VolumeUsage\.gp2$|VolumeUsage$", "gp2 volumes — gp3 is ~20% cheaper"),
    (r"(BoxUsage|InstanceUsage|NodeUsage)[^:]*:[a-z.]*(t2|m4|m3|c4|c3|r4|r3|i2)\.", "previous-gen instances — newer ones cost less"),
    (r"DataProcessing-Bytes", "CloudWatch Logs ingestion — check log levels"),
    (r"PublicIPv4:InUseAddress", "public IPv4 — $3.60/mo per address"),
    (r"NatGateway-Hours", "NAT gateways — need one in every VPC/AZ?"),
]

AI_INSTRUCTIONS = (
    "This is an AWS cost breakdown exported by awstree. All amounts are USD for the metric named in `metric`. "
    "`current` is the most recent period and `previous` is the equally long period before it. "
    "Line items exclude credits and refunds; `bill` has the full picture including those. "
    "Usage types often start with a region code (USE1 = us-east-1, USW2 = us-west-2, EUC1 = eu-central-1, ...). "
    "Please: 1) explain what drives the cost, 2) explain notable changes vs the previous period, "
    "3) suggest concrete savings, each with an estimated monthly saving and how to verify it."
)


def summarize(data):
    """Turn the raw daily data into a compact JSON an AI agent can reason about."""
    days, split = data["days"], data["split"]
    n = len(days) - split

    def money(v):
        return round(v, 2)

    def totals(daily):
        return sum(daily[split:]), sum(daily[:split])

    def entry(cur, prev):
        return {
            "current": money(cur), "previous": money(prev), "change": money(cur - prev),
            "change_pct": round(100 * (cur - prev) / prev, 1) if prev >= 0.01 else None,
            "share_pct": round(100 * cur / grand, 2) if grand else 0,
        }

    def region_label(r):
        return "global" if not r or r == "NoRegion" else r

    def grouped(view, label=lambda k, names: k):
        v = data["views"][view]
        out = {}
        for r in v["rows"]:
            cur, prev = totals(r["d"])
            g = out.setdefault(label(r["k"][0], v["names"]), {"cur": 0, "prev": 0, "items": {}})
            g["cur"] += cur
            g["prev"] += prev
            it = g["items"].setdefault(r["k"][1], [0, 0])
            it[0] += cur
            it[1] += prev
        return sorted(out.items(), key=lambda kv: -kv[1]["cur"])

    def keep(cur, prev):
        return abs(cur) >= 0.01 or abs(prev) >= 0.01

    services = grouped("service")
    grand = sum(g["cur"] for _, g in services)
    grand_prev = sum(g["prev"] for _, g in services)

    line_items = []
    for svc, g in services:
        for ut, (cur, prev) in g["items"].items():
            if keep(cur, prev):
                line_items.append({"service": svc, "usage_type": ut, **entry(cur, prev)})
    line_items.sort(key=lambda x: -x["current"])

    def breakdown(view, key_name, label=lambda k, names: k):
        names = data["views"][view].get("names", {})
        out = []
        for k, g in grouped(view, label):
            if not keep(g["cur"], g["prev"]):
                continue
            row = {key_name: k}
            if names.get(k):
                row["name"] = names[k]
            row.update(entry(g["cur"], g["prev"]))
            row["services"] = sorted(({"service": s, **entry(c, p)} for s, (c, p) in g["items"].items() if keep(c, p)),
                                     key=lambda x: -x["current"])
            out.append(row)
        return out

    growers = [x for x in line_items
               if x["change"] >= max(1, grand * 0.005) and (x["change_pct"] is None or x["change_pct"] > 20)]
    growers = sorted(growers, key=lambda x: -x["change"])[:10]
    flags = []
    for x in line_items:
        for pattern, why in PITS:
            if re.search(pattern, x["usage_type"]) and x["current"] >= grand * 0.002:
                flags.append({"service": x["service"], "usage_type": x["usage_type"], "current": x["current"], "reason": why})
                break

    daily = [0.0] * len(days)
    for r in data["views"]["service"]["rows"]:
        for i, v in enumerate(r["d"]):
            daily[i] += v

    return {
        "tool": "awstree",
        "instructions_for_ai": AI_INSTRUCTIONS,
        "generated": data.get("generated"),
        "profile": data.get("profile"),
        "metric": data.get("metric", "UnblendedCost"),
        "demo_data": bool(data.get("demo")),
        "period": {
            "current": {"start": days[split], "end": days[-1], "days": n},
            "previous": {"start": days[0], "end": days[split - 1], "days": split},
        },
        "totals": {**entry(grand, grand_prev), "daily_avg": money(grand / n), "monthly_pace": money(grand / n * 30.4)},
        "bill": {k: money(v) for k, v in data.get("bill", {}).items()},
        "by_service": [{"service": k, **entry(g["cur"], g["prev"])} for k, g in services if keep(g["cur"], g["prev"])],
        "by_account": breakdown("account", "account_id"),
        "by_region": breakdown("region", "region", lambda k, names: region_label(k)),
        "top_growers": growers,
        "flags": flags,
        "line_items": line_items,
        "daily_totals": [{"date": d, "cost": money(v)} for d, v in zip(days, daily)],
    }


def export(data, path):
    path.write_text(json.dumps(summarize(data), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------- output

def render(data, out):
    html = TEMPLATE.read_text()
    page = {**data, "pits": PITS, "export": summarize(data)}
    blob = json.dumps(page, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    out.write_text(html.replace("__AWSTREE_DATA__", blob), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="See where your AWS money goes, as a treemap.")
    ap.add_argument("--demo", action="store_true", help="use fake data (no AWS access needed)")
    ap.add_argument("--profile", help="AWS profile to use (default: your default credentials)")
    ap.add_argument("--days", type=int, default=30, help="period to show, compared with the period before it (default 30)")
    ap.add_argument("--metric", default="UnblendedCost",
                    choices=["UnblendedCost", "AmortizedCost", "NetUnblendedCost", "NetAmortizedCost", "BlendedCost"])
    ap.add_argument("--from", dest="source", metavar="JSON", help="re-open saved data (out/awstree-data.json) without calling AWS")
    ap.add_argument("--out", default=str(OUT / "awstree.html"), help="where to write the page (default out/awstree.html)")
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    ap.add_argument("--export", nargs="?", const=str(OUT / "awstree-export.json"), metavar="FILE",
                    help="write a summary JSON for an AI agent (default out/awstree-export.json) instead of the page")
    args = ap.parse_args()

    if not 1 <= args.days <= 180:
        die("--days must be between 1 and 180 (Cost Explorer keeps ~14 months of daily data).")

    if args.source:
        data = json.loads(Path(args.source).read_text())
    elif args.demo:
        data = demo(args.days)
    else:
        print(f"awstree: reading the last {args.days} days (+{args.days} before, for comparison) from Cost Explorer")
        data = fetch(args.profile, args.days, args.metric)
    if not args.source:
        data.update(generated=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), metric=args.metric,
                    profile=args.profile or "default")
        if not args.demo:
            OUT.mkdir(exist_ok=True)
            (OUT / "awstree-data.json").write_text(json.dumps(data))

    if args.export:
        path = Path(args.export).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        export(data, path)
        print(f"awstree: wrote {path}  (give this file to your AI agent)")
        return

    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    render(data, out)
    print(f"awstree: wrote {out}")
    if not args.no_open:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
