# AVIR AI: pricing plan for image and video creation

A proposal for how to charge for posts, images and videos, in Indian rupees
and US dollars, with the limits that go with each plan. It looks at pricing
from five angles (what it costs us, what competitors charge, what each type of
customer gets out of it, India versus the rest of the world, and abuse risk),
then recommends plans and explains how to build them in this codebase.

Written October 2026. All numbers are a starting point to test, not final
prices.

---

## 1. The short version

- **Video is the expensive part.** A 9-second clip costs us about ₹600
  ($6.30) and a 15-second clip about ₹1,000 ($10.50). Video should never be
  "unlimited" or bundled freely; sell it as clip packs, and only to accounts an
  admin has switched video on for (already how the app works).
- **Images are expensive at today's settings.** The configured price is $0.55
  (about ₹52) per image. At that price an image has to sell for at least
  ₹134 / $1.43 to keep a 60% margin, so plans can include only a few. If a
  cheaper image model brings the cost near $0.05, plans can include ten times
  more. **Checking the real image price on HeyRoute is the single most useful
  next step** (section 2).
- **Text is cheap.** About ₹2.4 ($0.025) per post, so posts can be generous.
- **The AWS servers add almost nothing per image or video** (well under 1%).
  They are a fixed monthly bill of about ₹4,940 ($52) that matters only while
  there are few paying users (section 2a).
- **Recommended plans** (section 8): Free, Starter ₹1,599 / $19, Growth
  ₹3,999 / $49, Pro ₹9,999 / $129 a month, and Enterprise on request, with
  image and video top-up packs. Each keeps a gross margin of 55 to 64% even if
  a customer uses every image in their plan.

---

## 2. What each creation costs us

| Item | Cost (USD) | Cost (INR at ₹95/$) | Where the number comes from |
|---|---|---|---|
| Text post (captions, hashtags, checks) | about $0.025 | about ₹2.4 | Measured: one LinkedIn post used about 42,500 tokens, about $0.025 (DEPLOYMENT.md) |
| Image (gemini-3.1-flash-image) | $0.55 | ₹52 | The price set in Admin → Image Settings (`HEYROUTE_IMAGE_COST_USD`). **Not yet checked against a HeyRoute bill** |
| Image edit | $0.55 | ₹52 | Same model, so same price; edits already count towards the image limit |
| Video, per second (minimax-h3-original-768p) | $0.70 | ₹66.5 | HeyRoute; confirmed by the $6.30 charge for a 9-second clip in the HeyRoute usage log |
| Video, 9-second clip | $6.30 | ₹599 | 9 × $0.70 |
| Video, 15-second clip | $10.50 | ₹998 | 15 × $0.70 |

On top of the AI cost:

| Cost | Rate used here |
|---|---|
| Card payments in USD (Stripe or similar, international cards) | about 4% of the price |
| Payments in INR (Razorpay: 2% plus 18% GST on that fee) | about 2.4% of the price |
| GST on Indian sales | 18%, **added on top** of the INR prices below and paid to the government, so it is not counted as our revenue |
| Exchange rate | ₹95 per $1 (the rate was about ₹95–96 in September 2026; review every quarter) |
| Hosting, email, support | not included; small per user, covered by the margin |

**Before setting final prices:** generate a few images, then check the
HeyRoute usage log the way the video price was checked. If the real image
price is lower than $0.55, update it in Admin → Image Settings and use
section 9 instead of section 8. The other image models on the same key
(`flux-klein-2`, `gpt-image-2`, `grok-imagine-image` and others) may also be
cheaper; their prices are on the HeyRoute pricing page, which needs a login.

---

## 2a. Full cost per image and per video, including AWS

### The AWS bill (fixed every month)

From the recommended setup in [DEPLOYMENT.md](DEPLOYMENT.md), on-demand prices
in `us-east-1` (Mumbai `ap-south-1` is usually a little higher):

| Item | USD / month | INR / month |
|---|---|---|
| EC2 `t3.medium` (4 GB, 2 web workers) | $30.40 | ₹2,888 |
| EBS disk, 30 GB | $2.40 | ₹228 |
| Elastic IP | $3.70 | ₹352 |
| RDS PostgreSQL `db.t4g.micro` | $11.70 | ₹1,112 |
| RDS storage and backups | $2.30 | ₹219 |
| ECR (Docker images) | under $0.50 | under ₹48 |
| S3 (generated images and videos) | under $1 | under ₹95 |
| Data transfer out | $0 for the first 100 GB | ₹0 |
| **Total** | **about $52** | **about ₹4,940** |

### One image, cost by part

| Part | USD | INR | Notes |
|---|---|---|---|
| AI image (HeyRoute, gemini-3.1-flash-image) | $0.5500 | ₹52.25 | Configured price; check against the HeyRoute log |
| Image prompt | $0 | ₹0 | Written as part of the post's text, already in the text cost |
| Logo overlay and resizing on EC2 | $0.00004 | ₹0.003 | About 3 seconds of server time |
| S3 storage for a year | $0.00014 | ₹0.013 | About 0.5 MB (the image plus a clean copy without the logo) |
| Data transfer, about 5 views | $0.00013 | ₹0.013 | Only once past the free 100 GB a month |
| **Total, excluding the server's fixed share** | **$0.5503** | **₹52.28** | |

### One video, cost by part

| Part | 9-second clip (USD / INR) | 15-second clip (USD / INR) | Notes |
|---|---|---|---|
| AI video (HeyRoute, $0.70 a second) | $6.30 / ₹598.50 | $10.50 / ₹997.50 | Confirmed against the HeyRoute log ($6.30 for 9 seconds) |
| Narration script (LLM) | $0.005 / ₹0.48 | $0.005 / ₹0.48 | The spoken line written into the video prompt |
| Watermark and crop on EC2 | $0.0035 / ₹0.33 | $0.0035 / ₹0.33 | About 5 minutes of server time; it also slows other requests while it runs |
| S3 storage for a year | $0.0007 / ₹0.07 | $0.0012 / ₹0.12 | About 2.7 MB and 4.5 MB |
| Data transfer, about 5 views | $0.0012 / ₹0.11 | $0.0020 / ₹0.19 | Only past the free 100 GB a month |
| **Total, excluding the server's fixed share** | **$6.31 / ₹599.50** | **$10.51 / ₹998.60** | |

### One text post

| Part | USD | INR |
|---|---|---|
| Writing, checking and hashtags (about 42,500 tokens) | $0.025 | ₹2.38 |
| Server time, storage | under $0.0001 | under ₹0.01 |
| **Total, excluding the server's fixed share** | **$0.025** | **₹2.38** |

### The server's fixed share

The $52 bill is the same whether 10 or 10,000 images are made, so its share
per item shrinks as usage grows:

| Generations a month (posts + images + videos) | Server share each (USD / INR) |
|---|---|
| 500 | $0.104 / ₹9.88 |
| 2,000 | $0.026 / ₹2.47 |
| 10,000 | $0.005 / ₹0.49 |
| 50,000 | $0.001 / ₹0.10 |

| Paying users | Server share per user a month (USD / INR) |
|---|---|
| 25 | $2.08 / ₹198 |
| 100 | $0.52 / ₹49 |
| 500 | $0.10 / ₹10 |

### Plan margins with everything included

The margins in section 8 count AI costs only. Adding the text cost of $0.025
a post and the server's share per paying user, at full use of the plan:

| Plan | Full cost with 50 paying users | Margin USD / INR | Full cost with 200 paying users | Margin USD / INR |
|---|---|---|---|---|
| Starter | ₹764 / $8.04 | 56% / 51% | ₹690 / $7.26 | 60% / 56% |
| Growth | ₹1,880 / $19.79 | 58% / 52% | ₹1,806 / $19.01 | 60% / 54% |
| Pro | ₹4,659 / $49.05 | 60% / 52% | ₹4,585 / $48.27 | 61% / 53% |

What this shows:
- **Images and videos are almost entirely AI cost.** AWS adds less than 0.1% to
  an image and well under 0.1% to a video; pricing should follow the HeyRoute
  price, not the server.
- **The server matters only while the business is small.** Below about 50
  paying users, INR plans run near a 50% margin at full use; by 200 users the
  server share is a few rupees per user.
- **Video has a hidden server cost:** each clip ties up a web worker for 10 to
  15 minutes while it renders and is processed. On a `t3.medium` with two
  workers, two videos at once leave nothing for other users. Before video is
  sold widely, move it to a background job (a queue and a separate worker) or a
  larger instance.
- **Assumptions:** file sizes, views per item and server seconds are estimates
  from this codebase's outputs, not measurements on the production server.
  Check the AWS bill after the first month and update this section.

### The free starting credit

New accounts now start with **$2 of credit (shown as ₹190 to Indian users)**,
down from $10. At today's costs that covers about 3 images and 14 text posts,
which matches the Free plan in section 8 (10 posts and 3 images cost about
$1.90). Video stays switched off for free accounts.

---

## 3. Perspective 1: cost (the floor)

The lowest price that still leaves a 60% gross margin, after payment fees. No
plan should sell an item below this for long.

| Item | Lowest price (USD) | Lowest price (INR) |
|---|---|---|
| Text post | $0.05 | ₹5 |
| Image at $0.55 cost | $1.43 | ₹134 |
| Image at $0.05 cost | $0.13 | ₹12 |
| 9-second video | $16.41 | ₹1,532 |
| 15-second video | $27.34 | ₹2,554 |

Why 60%: software businesses usually aim for 70 to 80% gross margin. AI
generation is a real cost every time a user clicks, so 60% is a sensible floor
for the AI-heavy parts, with text posts carrying a much higher margin.

---

## 4. Perspective 2: the market (the ceiling)

What users can pay elsewhere, from 2026 review and pricing sites. Prices on
these sites often disagree, so treat them as rough.

| Product | What it includes | Price |
|---|---|---|
| Canva Pro (India) | Design, AI images, simple scheduling, one user | about ₹499 a month or ₹3,999 a year; about $12–18 a month elsewhere |
| Predis.ai | Whole AI posts (image, caption, hashtags), about 60 posts a month on the entry plan | about $19–32 a month; higher plans $79 and $249 |
| Ocoya | AI captions, scheduling, capped AI credits | from about $15 a month |
| Buffer | Scheduling with an AI writing helper, no AI images | about $5–6 per channel a month |
| Hootsuite | Full suite for teams | about $99 a month for one user |

What this means:
- **Starter has to sit near Canva and Predis.ai** (₹1,000–2,000 in India,
  $15–30 elsewhere). Above that, a solo user will pick the cheaper tool.
- **AVIR AI's brand grounding and compliance checks justify a higher price
  for businesses** (Growth and Pro), closer to Hootsuite than to Canva.
- **Few competitors sell AI video at all**, and Canva's is lower quality, so
  video can carry a premium price as an add-on.

---

## 5. Perspective 3: who is buying (value)

The app already has four account types. Each one wants something different.

| Account type | Who | What they care about | What to sell them |
|---|---|---|---|
| Individual | Creators, freelancers, consultants | Low monthly cost, quick posts | Starter: plenty of text posts, a few images, video only as an occasional extra |
| Small business | Shops, clinics, local brands | Posts that look professional without a designer | Growth: more images, festival and trending ideas, approvals |
| Medium business / agency | Marketing teams, agencies | Volume, several reviewers, compliance | Pro: high limits, video packs at a lower per-clip price |
| Enterprise | Banks, brokers, larger brands | Compliance, control, invoices | Enterprise: custom limits, contract, priority video |

How users judge value:
- **Against a designer.** A designed post from a freelancer typically costs
  far more than an AI image, so images are good value to a small business even
  at ₹100–150 each. (Assumption to check with customers; not measured here.)
- **Against time.** The ideas feed, calendar and compliance checks save
  planning time, which matters most to small businesses with no social media
  person.
- **Video is a "special occasion" item** (a launch, a festival), not a daily
  one, so users accept paying per clip.

---

## 6. Perspective 4: India versus the rest of the world

- **Set INR prices on their own, not by converting dollars.** At ₹95/$, $19
  would be ₹1,805; ₹1,599 is about 16% cheaper in dollar terms and sits better
  next to Canva in India. This is normal practice ("purchasing power" pricing).
- **Keep the India discount modest on AI items.** Our AI cost is in dollars,
  so a cheap rupee price on images or video loses money. The INR margins in
  section 8 are a few points lower than the USD ones on purpose, and never
  below 55%.
- **Show "+ 18% GST" next to INR prices** for business buyers, who can claim
  it back. Show a GST-inclusive price on the checkout page.
- **Annual plans matter more in India.** Offer annual billing at the price of
  10 months (two months free).
- **Payments:** Razorpay (UPI, cards, net banking) for INR; Stripe for USD.
  UPI AutoPay suits monthly plans in India.
- **Review rupee prices every quarter** against the exchange rate; a weaker
  rupee raises our cost per image and video.

---

## 7. Perspective 5: risk and limits

AI generation costs money on every click, so limits protect the business.

| Risk | Limit that handles it |
|---|---|
| One user generating hundreds of images in a day | Daily image cap per plan (already in the app: "Images today: 0 of 2") |
| Video costs running away | Video off by default; admin switches it on per user (built); a daily video cap (built: 3 per day); video sold only in prepaid packs |
| Free accounts used as a free image service | Small free limits, no video, verified email required |
| Users regenerating the same image many times | Edits and regenerations count as images (already how the image limit counts) |
| Failed generations | Never charge for a failed image or video; only successful ones count (already true: costs are logged only on success) |
| Exchange rate moves | Quarterly review of INR prices |

---

## 8. Recommended plans (at today's image cost of $0.55)

### Monthly plans

| | Free | Starter | Growth | Pro | Enterprise |
|---|---|---|---|---|---|
| **Price (India)** | ₹0 | ₹1,599 + GST | ₹3,999 + GST | ₹9,999 + GST | On request |
| **Price (rest of world)** | $0 | $19 | $49 | $129 | On request |
| **Annual (10 months' price)** | – | ₹15,990 / $190 | ₹39,990 / $490 | ₹99,990 / $1,290 | Contract |
| Best for | Trying it out | Individuals | Small businesses | Agencies, medium businesses | Regulated and large brands |
| Posts a month | 10 | 60 | 200 | 600 | Custom |
| Images a month (incl. edits) | 3 | 10 | 25 | 60 | Custom |
| Images per day (cap) | 1 | 5 | 10 | 25 | Custom |
| Video | – | Packs only | Packs only | Packs, 20% off | Included allowance |
| Videos per day (cap) | – | 1 | 2 | 3 | Custom |
| Brands (websites) | 1 | 1 | 1 | Up to 5 (once built) | Custom |
| Ideas, calendar, weekly email | ✓ | ✓ | ✓ | ✓ | ✓ |
| Compliance checks | ✓ | ✓ | ✓ | ✓ | ✓ + custom rules |
| Approvals | – | – | ✓ | ✓ | ✓ |
| Support | Help centre | Email | Email | Priority | Account manager |

### Margins if a customer uses every post and image in the plan

| Plan | Our cost (full use) | Margin (USD price) | Margin (INR price) |
|---|---|---|---|
| Free | ₹176 / $1.85 a month | – (acquisition cost) | – |
| Starter | ₹636 / $6.70 | 63% | 59% |
| Growth | ₹1,686 / $17.75 | 62% | 57% |
| Pro | ₹4,275 / $45.00 | 64% | 56% |

Most users don't use their whole allowance, so real margins will be higher.
Annual plans earn about 17% less per month, which lowers the full-use margin
by roughly 7 points; still above 50%.

### Top-up packs (any paid plan)

| Pack | India | Rest of world | Our cost | Margin (USD / INR) |
|---|---|---|---|---|
| 10 images | ₹1,299 | $15 | ₹522 / $5.50 | 62% / 59% |
| 50 images | ₹5,999 | $69 | ₹2,613 / $27.50 | 58% / 55% |
| 1 video, 9 seconds | ₹1,499 | $16.99 | ₹599 / $6.30 | 61% / 59% |
| 1 video, 15 seconds | ₹2,499 | $27.99 | ₹998 / $10.50 | 61% / 59% |
| 5 videos, 9 seconds | ₹6,999 | $79 | ₹2,993 / $31.50 | 58% / 56% |

Packs are prepaid, don't expire for 12 months, and are used only after the
plan's monthly allowance runs out. Pro gets 20% off video packs as its main
reason to upgrade (Pro's video margin is then about 48% in USD and 45% in INR;
acceptable for the largest accounts, or drop the discount if margin matters more).

---

## 9. If images become cheaper (about $0.05 each)

If a cheaper image model is good enough, image allowances can grow tenfold at
the same prices.

| Plan | Images a month | Our cost (full use) | Margin USD / INR |
|---|---|---|---|
| Starter (₹1,599 / $19) | 60 | ₹399 / $4.20 | 77% / 74% |
| Growth (₹3,999 / $49) | 200 | ₹1,330 / $14.00 | 70% / 66% |
| Pro (₹9,999 / $129) | 600 | ₹3,990 / $42.00 | 66% / 59% |

One option is to offer both: a "standard" image (cheap model) counted
normally, and a "premium" image (today's model) that counts as several
standard images. The app already supports different models with different
prices per image (Admin → Image Settings), so this is mostly a counting change.

---

## 10. How the limits should behave

- **Monthly allowances reset on the billing date**; daily caps reset at
  midnight UTC (as the image limit does today).
- **Unused allowance does not roll over**; packs do (for 12 months).
- **What counts:** a successful image or image edit counts as one image; an
  image command that makes several sizes counts each size; a video counts by
  its length in seconds. Text refinements of the same post don't count as new
  posts.
- **At the limit:** the option is switched off with a clear message and a
  "Buy more" button (the app already blocks the Image option at the daily
  limit; add the button).
- **Before any video:** show the price ("This 9-second video uses 1 video
  credit") so nobody is surprised.
- **Failed generations are free.**

---

## 11. How to build it in this codebase

What already exists and can be reused:

| Exists today | Use it for |
|---|---|
| `users.credit_limit` (USD) and credit requests | Becomes the plan's allowance, or stays as a spending cap for Enterprise |
| Daily image limit and per-user override (Admin → Image access) | The daily caps per plan |
| Video access switch and daily video limit | Video only for paid plans with a pack |
| `account_type` (individual, small, medium, enterprise) | Default plan for each account type |
| Image prices per model (Admin → Image Settings) and `HEYROUTE_VIDEO_COST_PER_SECOND_USD` | Our cost per item, for margin reports |
| `image_generations` table (every image and video with its cost) | Counting usage against the monthly allowance |
| Admin cost history | Margin per customer |

To add:

1. **Plans table**: name, prices in INR and USD (monthly, annual), monthly
   posts, images, video seconds, daily caps, features. Editable in Admin so
   prices can change without a release.
2. **Subscriptions table**: user, plan, currency, status, current period
   start and end, payment provider ids.
3. **Pack balances**: images and video seconds bought, used, expiry.
4. **Usage check** in one place before every generation: monthly allowance
   first, then packs, then the daily cap. The existing image-quota and
   video-quota checks in `api/routes.py` are where it plugs in.
5. **Payments**: Razorpay for INR (subscriptions plus UPI AutoPay) and Stripe
   for USD; webhooks update the subscription; GST invoice for Indian
   customers.
6. **Screens**: a public pricing page (INR or USD by country, with a switch),
   a Billing page in Settings (plan, usage bars, invoices, buy packs), and
   "Buy more" at every limit message.
7. **Admin**: plan editor, a customer's plan and usage, refunds, and margin by
   plan from the cost history.

Suggested order: plans and usage counting first (with plans assigned by
admins, no payments), then Razorpay, then Stripe, then packs.

---

## 12. Decisions to make

1. **Image model**: keep the $0.55 model, move to a cheaper one, or offer both
   as standard and premium (section 9).
2. **Free plan**: keep it (costs up to ₹176 per active free user a month) or
   offer a 7-day trial of Growth instead.
3. **Video length**: 9 seconds (cheaper, fits more budgets) or 15 seconds as
   the default.
4. **GST display**: "+ GST" everywhere, or GST-inclusive prices for individual
   buyers.
5. **Pro video discount**: keep the 20% (lower margin) or drop it.

---

## Sources

- Exchange rate, about ₹95–96 per $1 in August and September 2026:
  [Dollar Rupee](https://dollarrupee.in/),
  [Long Forecast](https://longforecast.com/usd-to-inr-forecast-2017-2018-2019-2020-2021-indian-rupee),
  [HDFC Sky](https://hdfcsky.com/news/rupee-rises-24-paise-to-close-at-95-46-against-us-dollar)
- Canva pricing in India: [Wiuwi guide](https://wiuwi.com/blogs/207324/Complete-Guide-to-Canva-Subscription-Pricing-in-India),
  [Piktochart, Canva pricing 2026](https://piktochart.com/blog/canva-pricing-2026-costs-raised/)
- Predis.ai pricing: [Capterra](https://www.capterra.com/p/231932/Predisai/pricing/),
  [AdMakeAI (competitor-written)](https://admakeai.com/alternatives-to/predis-ai/pricing)
- Other competitor prices: see the sources in [RESEARCH.md](RESEARCH.md)
- Our costs: this codebase (`config.py`, Admin → Image Settings) and the
  HeyRoute usage log
