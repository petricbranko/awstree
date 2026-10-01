# awstree

**Where did my AWS money go?** awstree reads your AWS bill and draws it as a treemap: big box, big cost.

![awstree showing a demo AWS bill as a treemap](docs/screenshot.png)

It's one Python file with no dependencies to install, and it produces a single HTML page that works offline.

## Quick start

```bash
git clone <this repo> && cd awstree
python3 awstree.py --demo                  # try it with fake data, no AWS needed
python3 awstree.py --profile my-profile    # your real bill
```

The page opens in your browser. It shows the last 30 days, compared with the 30 days before. Switch to **Day over day** to inspect any day in that range against the preceding UTC day.

## What you get

- **Treemap** of spend by service → usage type. Press `2` to group by account and `3` to group by region.
- **Color by change** (`c`): red where spend grew, green where it shrank.
- **Day-over-day review** (`d`): pick a UTC date or click a chart bar to compare it with the preceding day. Use the arrow keys to move through dates.
- **Selection panel:** cost, share of the bill or day, previous-period or previous-day change, and a clickable daily chart.
- **Worth a look:** the fastest-growing costs plus common money pits: NAT data processing, idle IPs, gp2 volumes, old snapshots, previous-gen instances and extended-support fees.
- **Export period:** a full-period JSON summary to hand to any AI agent (see below).

Click to select, double-click to zoom in, `⌫` to go back up, `/` to filter.

## Requirements

- Python 3.8+, plus the [AWS CLI](https://aws.amazon.com/cli/) or `boto3`
- Credentials with the `ce:GetCostAndUsage` permission. Run it from the management (payer) account to see every account in your organization.
- Cost Explorer enabled in the Billing console. The very first time, it can take up to 24 hours before data shows up.

**Cost:** AWS charges $0.01 per Cost Explorer request, and a run makes about 4–10 requests. awstree saves the data, so reopening the page is free:

```bash
python3 awstree.py --from out/awstree-data.json
```

## Ask an AI about your bill

```bash
python3 awstree.py --from out/awstree-data.json --export
```

This writes `out/awstree-export.json`, a compact full-period summary (about 50 KB) that includes instructions for the agent. It has totals, breakdowns by service, account and region, every line item with its change vs the previous period, the top growers and the flagged money pits. Give it to an AI agent and ask *"where can I save money?"*. The **Export period** button in the page (or `e`) downloads the same file.

## Options

| Flag | What it does |
|---|---|
| `--demo` | Fake data, no AWS access needed |
| `--profile NAME` | AWS profile to use (SSO and `aws login` work) |
| `--days N` | Period length, default 30. It's always compared with the period before it. |
| `--metric M` | `UnblendedCost` (default), `AmortizedCost`, `NetUnblendedCost`, `NetAmortizedCost` |
| `--from FILE` | Reopen saved data without calling AWS |
| `--export [FILE]` | Write the AI summary JSON instead of the page |
| `--out FILE` | Where to write the page (default `out/awstree.html`) |
| `--no-open` | Don't open the browser |

## Privacy

awstree only reads from Cost Explorer and never changes anything in your account. Everything it writes goes to `out/`, which is git-ignored because it contains your account IDs and costs. The page loads nothing from the internet. Share the HTML or export file only with people who should see your bill.

## Credits

Inspired by [disktree](https://x.com/tobi/status/2103251521223921739) by [Tobi Lütke](https://x.com/tobi): the same idea, pointed at a cloud bill instead of a disk.

awstree is not affiliated with or endorsed by Amazon Web Services.

## License

[MIT](LICENSE)
