---
title: Social accounts and publishing
summary: Connect Instagram, Facebook and LinkedIn, then publish or schedule posts.
order: 6
icon: fa-plug
---

Connect your accounts once in [Social accounts](/settings). After that, posts can be published from AVIR AI: straight away from an approved request, or by themselves at a time you choose. See [Scheduling](/help/scheduling).

## The quick way: Connect with Facebook or LinkedIn

If you see a **Connect with Facebook** or **Connect with LinkedIn** button on a card:

1. Click it. You are taken to Facebook or LinkedIn.
2. Sign in there and approve. On Facebook, tick the Page you want to post to.
3. You come back to Social accounts, connected. There is nothing to copy.

- **Facebook** connects your Page and the Instagram account linked to it in one go.
- **LinkedIn** connects your own profile. If you manage company pages and they are available, you choose which one to post as.
- If you manage several Pages, you are asked which one to use.

A LinkedIn connection lasts 60 days. The card shows the date, and you get a notification under the bell a week before. Click **Connect with LinkedIn** again to renew it.

> Note: these buttons appear once your administrator has set them up. Until then, use **Configure** and enter the details by hand, as described below. That way always remains available as **Enter details by hand**.

## Connecting Instagram

Instagram needs two things first:

1. An **Instagram Business or Creator account**. Personal accounts can't be published to by any app.
2. That account **linked to a Facebook Page**. Do this in Meta Business Suite, under Settings, Instagram accounts.

Then, in Social accounts:

- If your Facebook Page is already connected, click **Use the account linked to my Facebook Page**. That's all.
- Otherwise click **Configure** on the Instagram card and paste your Facebook Page ID and its Page access token. AVIR AI finds the Instagram account and fills in the handle.

Click **Verify API** to check the connection. It also shows how many posts you've published through apps in the last 24 hours.

## Connecting Facebook and LinkedIn

Click **Configure** on each card and enter the Page or account ID and its access token, then **Verify API**.

- **Facebook**: the Page ID and a Page access token that is allowed to post (`pages_manage_posts`).
- **LinkedIn**: your Author URN (`urn:li:person:...` for a profile, `urn:li:organization:...` for a company page) and an access token that is allowed to post (`w_member_social` or `w_organization_social`).

## What each platform can publish

| Platform | Text only | One image | Carousel |
|---|---|---|---|
| Instagram | No | Yes | 2 to 10 slides, all the same shape |
| Facebook | Yes | Yes | 2 to 10 photos in one post |
| LinkedIn | Yes | Yes | 2 to 20 images, or a document (PDF) people swipe through |

> Note: a LinkedIn account connected through your own MCP server can publish text posts only. Connect LinkedIn directly to publish images and carousels.

## Publishing an approved post

1. Send the post for approval and get the platform approved. See [Approvals](/help/approvals).
2. Open the request in [Approval requests](/approve).
3. Click **Publish to** your account on the platform's tab. It goes live immediately.
4. The request then shows **Published**, with a link to the post.

Only the person who asked for approval sees the publish button, and each platform can be published once.

## If something goes wrong

| Message | What to do |
|---|---|
| "The connection has expired" or "no longer accepts the saved access token" | Connect the account again in Social accounts. |
| "No Instagram Business or Creator account linked" | Link the account to your Facebook Page in Meta Business Suite. |
| "Posting limit has been reached" | The platform limits posts per day from apps. Try again later. |
| "Instagram posts need an image" | Instagram can't publish text only. Add an image. |
| "isn't allowed to post to this Page" | The Facebook token needs the `pages_manage_posts` permission. |
| "needs permission to post as this author" | The LinkedIn token can't post for that profile or company page. |
