# Instagram carousel publishing

Each newly generated story is published automatically to
**@bedtime01fables** as an Instagram **image carousel**: the story's own
`cover_final.png` (or `cover.png` as a fallback) followed by `slide_1.png …
slide_N.png`, in reading order. It never publishes Reels or video.

Live publishing is **off** until the repository variable
`INSTAGRAM_AUTO_PUBLISH_ENABLED` is set to `true`.

## How it works

```
Daily Story Generator (unchanged) ── completes successfully ──▶ Publish Story to Instagram
  generate → retention → push main                                  │
                                                                     ├─ verify account (@bedtime01fables, Business/Creator)
                                                                     ├─ pick eligible stories (oldest first)
                                                                     ├─ PNG → JPEG (temp dir) → Cloudinary
                                                                     ├─ item containers → CAROUSEL container → wait FINISHED
                                                                     ├─ record publish_requested ─▶ media_publish
                                                                     ├─ record published (media id, permalink)
                                                                     └─ delete hosted images
```

* **Trigger:** `workflow_run` when *Daily Story Generator* completes successfully on
  `main`, plus manual `workflow_dispatch` (defaults to a dry run). There's no
  schedule, so each generated story gets exactly one automatic trigger.
* **API:** Meta's *Instagram API with Instagram Login* (`graph.instagram.com`),
  permissions `instagram_business_basic` + `instagram_business_content_publish`.
  The API version comes from the variable `INSTAGRAM_GRAPH_API_VERSION`; when it
  isn't set, the code uses `DEFAULT_API_VERSION` in `services/instagram_graph_client.py`.
* **Limits enforced** (Meta's docs at the time of writing): JPEG only, ≤ 8 MB,
  aspect ratio 4:5 to 1.91:1, width 320–1440, 2–20 carousel items, caption ≤ 2200
  characters, ≤ 30 hashtags, ≤ 20 @-tags. The daily pipeline produces 1080×1350 PNGs,
  so the only adaptation is re-encoding them to JPEG with the same pixels. A story
  with more than 20 images is rejected rather than truncated.
* **Caption:** title, the story's short description, `🌟 Lesson: <moral>`, a swipe
  prompt, the brand footer, and up to 15 de-duplicated hashtags.

### Eligibility

A story is published only if all of these hold:

1. It has no `published` or in-progress record in the publication history.
2. It was created on or after `live_enabled_on`, the date the first live run
   recorded. Turning publishing on therefore never back-fills historical stories.
3. It was created within `INSTAGRAM_MAX_STORY_AGE_DAYS` (default 1) of today,
   UTC. A manual run with a `content_id` skips this check, but never checks 1 or 2.
4. Its folder has a cover and contiguous slides.

At most `INSTAGRAM_MAX_POSTS_PER_RUN` stories (default 3) are published per run.

### Duplicate protection and publication history

The history is `instagram_publications.json` on the dedicated branch
**`instagram-publication-state`**. It is never written to `main`, which means:

* Retention, which only touches `output/` and `data/content_library.json` on
  `main`, can't erase a record or make an old story eligible again.
* It can never make the Daily Story workflow's `git push` to `main` fail.
* Every write is a compare-and-swap: a non-force push on top of the exact commit
  that was read. A concurrent writer's push is rejected, so the run re-reads the
  history and re-checks before continuing. The workflow also runs one job at a
  time (`concurrency: instagram-publish`).

> `data/content_library.json`'s `instagram.posted` field is always written as
> `true` by the story pipeline, so it is **not** used for publishing decisions.

Each record stores the status (`claimed`, `containers_ready`,
`publish_requested`, `published` or `failed`), the attempt count and
timestamps, the container IDs, the media ID and permalink, the caption and its
fingerprint, the hosted asset IDs, the last error (already redacted), and a short
event history.

**Uncertain results:** `publish_requested` is saved *before* `media_publish` is
called. If that call times out or returns 5xx, the publisher never simply calls it
again. It checks the carousel container's `status_code` first:

* `PUBLISHED`: the publish succeeded. It's recorded as published.
* `FINISHED`: the publish provably did not happen. The same container is published
  again; containers are single-use, so this can't create a second post.
* `EXPIRED` or `ERROR`: not published, unless the caption is found among the
  account's recent media. The story is marked failed and retried with new containers.
* Unknown: the record stays `publish_requested`, and the next run checks again.

**Hosted image retention:** Cloudinary public IDs are fixed per story and slide
(`bedtime01fables/instagram-carousel/<content_id>/<NN>`), so a retry overwrites
the previous files. Files are deleted once the post is confirmed published, or
48 hours after a failed attempt, when its containers are long expired. They are
never deleted while a container that references them could still be published.

## One-time setup

### 1. Check the account type

In the Instagram app, signed in as @bedtime01fables, go to **Profile → ☰ →
Settings and activity → Account type and tools**.

* If you see **Switch to professional account**, the account is **Personal**.
  Publishing through the API needs a professional account.
* If you see **Switch account type** or **Switch to personal account**, it's
  already professional. *Professional dashboard* on the profile also confirms this.

To switch, tap **Switch to professional account**, then pick **Creator**
(simplest for a single brand account) or **Business**. Either works, and neither
needs a Facebook Page with this login route. Nothing in this repository changes
account settings.

Once a token exists (step 2), the workflow checks this on every run with
`GET /me?fields=user_id,username,account_type`. The value must be
`MEDIA_CREATOR` or `BUSINESS`.

### 2. Meta app and access token (Instagram API with Instagram Login)

1. Go to <https://developers.facebook.com/apps> → **Create app**. Choose the
   Instagram use case (*Manage messaging & content on Instagram*) and a
   **Business** app type.
2. In the app, open **Instagram → API setup with Instagram business login**.
3. Under **Generate access tokens**, click **Add account** and sign in as
   **@bedtime01fables**. Grant **instagram_business_basic** and
   **instagram_business_content_publish**. If the app is in Development mode
   and the account isn't listed, add it under **App roles → Roles → Instagram
   Testers**, then accept the invite in the Instagram app (**Settings → Apps and
   websites → Tester invites**).
4. Copy the generated **access token** (a long-lived Instagram User token, valid
   60 days) and the **Instagram account ID** shown next to the account.
5. App Review / Advanced Access isn't needed to publish only to an account that
   has a role on your own app (Standard Access). Check the current requirements
   in the dashboard before you switch the app to Live mode.

### 3. Cloudinary

1. Create a free account at <https://cloudinary.com>.
2. Copy the **Cloud name**, **API key** and **API secret** from **Settings →
   API Keys**.
3. Nothing else is required. Uploads are signed and delivered from public
   `image/upload` URLs, which Instagram must be able to fetch.

### 4. GitHub Actions secrets and variables

**Settings → Secrets and variables → Actions**

| Kind | Name | Value |
|---|---|---|
| Secret | `INSTAGRAM_ACCESS_TOKEN` | the token from step 2 |
| Secret | `INSTAGRAM_USER_ID` | the Instagram account ID from step 2 |
| Secret | `CLOUDINARY_CLOUD_NAME` | from step 3 |
| Secret | `CLOUDINARY_API_KEY` | from step 3 |
| Secret | `CLOUDINARY_API_SECRET` | from step 3 |
| Variable | `INSTAGRAM_AUTO_PUBLISH_ENABLED` | `true` to go live (anything else, or unset, means dry run) |
| Variable (optional) | `INSTAGRAM_EXPECTED_USERNAME` | default `bedtime01fables` |
| Variable (optional) | `INSTAGRAM_GRAPH_API_VERSION` | e.g. `v26.0` |
| Variable (optional) | `INSTAGRAM_MAX_STORY_AGE_DAYS` | default `1` |
| Variable (optional) | `INSTAGRAM_MAX_POSTS_PER_RUN` | default `3` |

Secrets are only passed to the live step. The dry-run step receives none.

If branch rules or rulesets restrict branch creation or pushes, allow
`github-actions[bot]` to push `instagram-publication-state`.

### 5. Dry run, then enable

1. **Actions → Publish Story to Instagram → Run workflow** with `dry_run`
   checked. The run summary lists each story's decision, the carousel order and
   sizes, and the caption. Nothing is uploaded or posted.
2. Turn on live publishing (this is the only change needed):

   ```
   gh variable set INSTAGRAM_AUTO_PUBLISH_ENABLED --body true
   ```

From then on, each successful Daily Story run publishes its new story
automatically, with no per-post approval. The first live run records
`live_enabled_on`, so stories created before that day are never published.

To pause, set `INSTAGRAM_AUTO_PUBLISH_ENABLED` to `false`.

## Token renewal (every ≤ 60 days)

Long-lived Instagram User tokens last 60 days. A token can be refreshed once
it's at least 24 hours old and still valid. Refresh it about every 50 days:

```
curl -s "https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=$INSTAGRAM_ACCESS_TOKEN"
gh secret set INSTAGRAM_ACCESS_TOKEN      # paste the new access_token when prompted
```

Run these from your own machine with the token in an environment variable,
never in a committed file. If the token has already expired, generate a new one
(step 2.3). With an expired or invalid token, runs fail at the account check
before claiming or uploading anything.

## Operations

* **Retry a failed story:** the next automatic run retries it while it's within
  the age window, up to 3 attempts. You can also run the workflow with
  `dry_run` unchecked and the story's `content_id`.
* **After 3 failed attempts:** fix the cause, then on the
  `instagram-publication-state` branch set that story's `attempt_count` to `0`
  and run the workflow manually with its `content_id`. Never edit a `published`
  record.
* **`uncertain` in the run summary:** do nothing. The next run checks the
  container status before taking any action.
