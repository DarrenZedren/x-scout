# X Scout v0.4.0 — Local X Link

## Quick Start

1. Download or clone X Scout.
2. Double-click `run_x_scout.bat`.
3. Create your own X Developer App at `https://console.x.com/`.
4. Enable OAuth 2.0 as a Native App / public client.
5. Set the callback URL to:

   `http://127.0.0.1:8765/callback`

6. Open X Scout and click **X LINK**.
7. Paste your OAuth 2.0 Client ID and click **CONNECT X**.
8. Authorise your X account in the browser.
9. Create or choose a Mission Profile.
10. Press **START SCOUT**.

No X Scout server is involved. Your credentials, database, and API usage remain on your own computer.



X Scout is a **local-first desktop X discovery and network-management tool**. There is no X Scout server, hosted account, subscription service, or central database.

Each copy runs independently on the user's own computer:

```text
USER'S COMPUTER
├── X Scout desktop app
├── local SQLite database
├── local mission profiles / decisions
├── user's own X Developer App credentials
└──────────────► X API
```

The interface is local HTML/CSS/JavaScript inside `pywebview`; Python + SQLite run the engine underneath.

## v0.4.0 — what changed

### X LINK button
The flight deck now has an **X LINK** control.

A user supplies the OAuth 2.0 **Client ID from their own X Developer App**, authorises their own X account in their browser, and Scout talks directly to X from that computer.

No X password is requested by Scout.

### OAuth 2.0 PKCE
Scout implements the X OAuth 2.0 Authorization Code flow with PKCE using:

- `tweet.read`
- `users.read`
- `follows.read`
- `follows.write`
- `offline.access`

Callback URL:

```text
http://127.0.0.1:8765/callback
```

The X Developer App must have that exact callback URL configured.

### Local credential handling

- Client ID: local Scout configuration only
- OAuth access / refresh tokens: local credential vault only
- Windows: token vault is protected with Windows DPAPI for the current Windows user
- No credentials are embedded in the source code or GitHub release
- No credentials are transmitted through an X Scout service because no such service exists

### Live mode
Once X is connected:

- START SCOUT uses the authenticated user's own X Developer App/API credits
- recent X search is live
- the 24-hour activity gate remains enforced
- People and Conversations remain capped at 25 each and must clear their quality floors
- repeated START sweeps return unseen results rather than re-presenting the same IDs
- the user's Following + Followers network is synced from X
- existing follows are excluded from People discovery

The first live sweep automatically synchronises Following + Followers before discovery so Scout does not recommend accounts the user already follows.

### Human-controlled social actions
FOLLOW and UNFOLLOW can now use the real X API **only after a human clicks and confirms the action**.

Scout does not autonomously follow or unfollow accounts.

### Network sync
The NETWORK view can now use **SYNC X** to retrieve the connected account's current:

- Following
- Followers
- Mutuals
- Follows-you-only
- You-follow-only
- Grace-period accounts
- Review candidates
- Protected follows

Manual list import remains available as an offline fallback.

## Existing Scout behaviour

### Quality floor — 25 is a ceiling, not a quota
Scout returns **up to 25 People** and **up to 25 Conversation openings**. It does not pad a weak scan with mediocre candidates.

Default floors:

- People: 64
- Conversations: 68

### Repeated START = unseen results
Seen IDs are remembered per Mission Profile.

- START #1 → best unseen candidates
- START #2 → next unseen candidates
- START #3 → next unseen candidates

There is **no giant candidate cache**. Every START performs a fresh provider sweep and suppresses IDs already presented for that Mission Profile.

`RESET SEEN` clears presentation memory without deleting Network state, NOT FOR ME decisions, or NEVER SHOW decisions.

### Human taste controls
People results include:

- FOLLOW
- NOT FOR ME
- NEVER

NOT FOR ME can optionally record a reason such as spammy, too salesy, ragebait, low quality, not a builder, engagement farmer, or too corporate. These reasons make small bounded adjustments to future scoring.

### Mission Profiles / Topic Finder
Users can describe the part of X they care about. Scout proposes Core Signals and exclusions.

Core Signals are removable with `×`, and exclusions remain editable per profile.

### Default discovery rules

- verified blue/business accounts only
- government/unverified rejected
- 100–50,000 followers
- preference toward 100–15,000 followers
- posted within 24 hours
- stronger recency boost under 12 hours
- strongest recency boost under 6 hours
- English
- active Mission Profile topics/exclusions

## First-run setup

### 1. Run Scout
Double-click:

```text
run_x_scout.bat
```

The BAT installs `pywebview` if required, then starts Scout.

Or PowerShell:

```powershell
python -m pip install -r requirements.txt
python launch.py
```

### 2. Create your own X Developer App
Go to:

```text
https://console.x.com/
```

Create an app and enable OAuth 2.0 as a Native App / public client.

Configure this callback URL exactly:

```text
http://127.0.0.1:8765/callback
```

Copy the app's OAuth 2.0 **Client ID**.

### 3. Connect inside Scout

- click **X LINK**
- paste the Client ID
- click **CONNECT X**
- approve the requested permissions in the browser
- return to Scout

Scout then synchronises the network and switches from DEMO to LIVE.

API credits and billing remain entirely inside that user's own X Developer account.

## Demo mode
If X is not connected, Scout remains fully usable in Demo Mode with synthetic data for testing the interface, scoring, Topic Finder, taste controls, and Network workflow.

## Verification

```powershell
VERIFY_X_SCOUT.bat
```

Runs the automated unit suite and a headless demo sweep.

## Project philosophy

**Local first. Human in command. Bring your own X Developer App. No central Scout service.**
