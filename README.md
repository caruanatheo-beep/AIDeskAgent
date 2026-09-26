---
title: AI Sales Desk Assistant
emoji: 📈
colorFrom: gray
colorTo: orange
sdk: gradio
sdk_version: 6.28.0
python_version: 3.12
app_file: app.py
pinned: false
license: mit
short_description: Source-backed macro and cross-asset intelligence for Markets Sales.
tags:
  - finance
  - macroeconomics
  - fixed-income
  - agentic-ai
  - gradio
---

# AI Sales Desk Assistant

AI Sales Desk Assistant is a Python and Gradio application designed for financial-markets Sales professionals. It combines live public-market data, official economic releases, macro events and a controlled AI research workflow in one desk-style interface.

The application is built to produce concise and auditable market intelligence. It separates observed data from interpretation, attaches sources to research outputs and preserves a deterministic fallback when model inference is unavailable.

Developed by **Théo Caruana**, MSc Financial Markets & Investments candidate at SKEMA Business School, with previous experience in Fixed Income and Derivatives Brokerage, Liquidity Sales and Global Markets Risk.

> This project uses public information only. It does not contain confidential employer data or proprietary research. It is intended for decision support and educational demonstration, not investment advice. Client-facing content requires human review.

## Main features

- Live cross-asset dashboard covering rates, FX, equities, volatility, commodities, credit and macro indicators.
- Official U.S. Treasury curve with previous-session comparisons.
- FRED integration with bounded fallbacks and historical charts.
- Recent official news from major central banks and economic institutions.
- Official macro and central-bank calendar with upcoming-event links.
- AI Desk Copilot for morning briefs, market-move analysis, event preparation and client talking points.
- Controlled tool selection, evidence registration, source auditing and session-level budgets.
- Local RAG over approved Federal Reserve, ECB, IMF and BIS publications.
- Deterministic evidence coverage score with no additional model call.
- Session-only conversational memory with a visible reset control.
- Professional PDF reports and email-ready drafts.
- Safe fallback research when the model or an external data provider is unavailable.

## Application sections

| Section | Purpose |
| --- | --- |
| Overview | Key market observations, yield curve, cross-asset chart and morning brief |
| Markets | Market monitor with levels, daily changes, freshness and sources |
| News | Recent official macro and central-bank publications |
| Research Agent | Source-backed analysis and client-ready market intelligence |
| Calendar | Upcoming official economic and policy events |
| Historical Charts | Up to six selected series over configurable lookback periods |
| Reports | PDF market reports with charts, catalysts, sources and email drafts |

## Research workflow

The Research Agent follows a bounded workflow:

1. Classify the request.
2. Select only the relevant research tools.
3. Collect current market data, official news, events and approved RAG passages.
4. Validate and deduplicate the evidence.
5. Calculate a transparent evidence coverage score.
6. Use a maximum of one model call to synthesize the answer.
7. Append the audited source list and retain a deterministic fallback.

The model does not calculate values already available to Python. Market changes, curve spreads, timestamps, source admission, evidence scores, charts and usage limits remain deterministic and testable.

## Data sources

- U.S. Treasury for official daily par yields.
- FRED for public macroeconomic, rates, policy, FX and credit series.
- Public market quotes for intraday cross-asset observations.
- Federal Reserve, European Central Bank, Bank of England and Bank of Japan official feeds.
- Official economic and central-bank schedules.
- Approved public research documents from the Federal Reserve, ECB, IMF and BIS.

Availability and publication frequency vary by provider. Missing observations remain explicitly unavailable and are never replaced with demonstration data.

## Repository structure

```text
.
├── app.py
├── requirements.txt
├── run_local.ps1
├── diagnose_fred.py
├── rag_documents/
│   ├── *.pdf
│   └── rag_index.json
├── src/
│   ├── agent.py
│   ├── analytics.py
│   ├── calendar.py
│   ├── config.py
│   ├── data.py
│   ├── evidence.py
│   ├── llm.py
│   ├── memory.py
│   ├── models.py
│   ├── news.py
│   ├── reports.py
│   ├── security.py
│   ├── tools.py
│   ├── ui.py
│   └── web_search.py
└── tests/
```

## Local installation

Python 3.12 is recommended for deployment. From PowerShell in the project directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

If Python 3.12 is not installed:

```powershell
py install 3.12
```

The application normally opens at [http://127.0.0.1:7860](http://127.0.0.1:7860). When this port is occupied locally, the entry point selects the next available port and prints the address in the terminal.

The included script can automate local setup and launch:

```powershell
.\run_local.ps1
```

## Environment variables

Create a local `.env` file in the repository root. Never commit this file.

Minimal Hugging Face configuration:

```dotenv
ENABLE_LLM=true
ALLOW_PUBLIC_LLM=false
LLM_PROVIDER=huggingface
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct:featherless-ai
HF_TOKEN=your_hugging_face_token
```

Optional configuration:

```dotenv
FRED_API_KEY=
REQUEST_TIMEOUT_SECONDS=8
CACHE_TTL_SECONDS=900
LIVE_CACHE_TTL_SECONDS=60
MAX_LLM_CALLS_PER_SESSION=5
LLM_COOLDOWN_SECONDS=10
MAX_AGENT_STEPS=4
MAX_TOOL_CALLS_PER_SESSION=18
MAX_CALLS_PER_TOOL=4
MAX_MEMORY_TURNS=6
MAX_CONTEXT_CHARS=18000
MAX_NEWS_ITEMS=10
ENABLE_WEB_SEARCH=true
WEB_SEARCH_MAX_RESULTS=5
```

An OpenAI-compatible endpoint can be used instead:

```dotenv
ENABLE_LLM=true
LLM_PROVIDER=openai_compatible
LLM_MODEL=your_supported_model
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.openai.com/v1
```

`FRED_API_KEY` is optional. The application can use public FRED routes and other public-source fallbacks when the key is absent.

## Tests

Run the deterministic test suite before each release:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The suite covers market calculations, data fallbacks, calendar and news parsing, agent tool selection, evidence admission, security controls, reports, UI construction and application startup.

To diagnose FRED independently from the interface:

```powershell
.\.venv\Scripts\python.exe diagnose_fred.py
```

## Hugging Face deployment

1. Create a new Hugging Face Space.
2. Select **Gradio** as the SDK and **Python 3.12** as the runtime.
3. Upload or push the repository with `app.py` and this `README.md` at the root.
4. Open **Settings**, then add credentials under **Secrets**.
5. Add non-sensitive limits under **Variables** if the defaults need to change.
6. Wait for the build to complete and review the runtime logs.
7. Test the dashboard, Research Agent, calendar, historical charts and report export.

Recommended Space secrets:

```text
HF_TOKEN
FRED_API_KEY          # optional
OPENAI_API_KEY        # only for an OpenAI-compatible provider
```

Recommended Space variables:

```text
ENABLE_LLM=true
ALLOW_PUBLIC_LLM=true
LLM_PROVIDER=huggingface
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct:featherless-ai
```

`ALLOW_PUBLIC_LLM=true` permits anonymous visitors to trigger model calls. Enable it only after setting provider budgets and session limits. When it is false, the dashboard and deterministic research fallback remain available.

## Security and cost controls

- Credentials are loaded from environment variables and redacted from errors.
- External URLs must use HTTPS and pass a domain allowlist.
- Private-network targets, unsupported ports and unsafe schemes are rejected.
- External content is treated as data, never as an instruction.
- Model calls, tool calls, context size, memory and search results are bounded.
- General questions use the existing dashboard snapshot; live web search is reserved for relevant causal or announcement requests.
- Client-ready conversion does not create a second model call.
- Conversation memory remains inside the active Gradio session and is not intentionally stored permanently.

## Known limitations

- Public quotes can be delayed and are not a substitute for a professional market-data terminal.
- Official macro observations may be published with a lag and may later be revised.
- Public-source connectivity can vary across local Windows, cloud and institutional networks.
- The calendar does not invent unavailable consensus or actual values.
- The local RAG corpus is intentionally limited to approved public documents.
- Model output may contain errors and must be reviewed before external distribution.
- The application does not provide investment advice, order execution or automatic client communication.

## Release workflow

Use `main` for the stable public version and a separate branch for each material update:

```powershell
git checkout -b feature/short-feature-name
git add .
git commit -m "Describe the feature"
git push -u origin feature/short-feature-name
```

After validation, merge into `main` and create a version tag:

```powershell
git checkout main
git pull
git tag v2.8.0
git push origin main --tags
```

## License

Released under the [MIT License](LICENSE).

## Author

**Théo Caruana**  
MSc Financial Markets & Investments, SKEMA Business School
