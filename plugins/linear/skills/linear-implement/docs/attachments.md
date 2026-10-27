# Attachments

How an agent adds screenshots, recordings, logs and other files to a Linear issue. The Linear MCP tools
never carry file bytes: the agent uploads them itself with `curl`.

## Attach Or Embed

| Goal | Tools | Result |
|---|---|---|
| Evidence on the issue | `prepare_attachment_upload` then `create_attachment_from_upload` | file listed under the issue's Resources |
| Image or file inside Markdown (description, comment, document) | `prepare_file_upload`, then the Markdown write | `assetUrl` or markdown in the body; not in Resources |

Written findings belong in the issue description or a Linear Document (`save_document`), not in a file.

## Attach A File

Do these steps for one file at a time.

1. Call `prepare_attachment_upload(issue, filename, contentType, size)` with the exact byte size. It returns
   `uploadRequest.url`, `uploadRequest.headers` and `assetUrl`. The result only lands in your context: write
   it verbatim to `prepare.json` with your file-write tool.
2. Upload the raw bytes within 60 seconds, building the headers from that file, never by hand
   (`Content-Type` is one of them):

   ```sh
   mapfile -t H < <(jq -r '.uploadRequest.headers | to_entries[] | "-H\n\(.key): \(.value)"' prepare.json)
   curl -sS --fail-with-body -X PUT --data-binary @shot.png "${H[@]}" "$(jq -r .uploadRequest.url prepare.json)"
   ```

   Any non-2xx status makes `curl` exit non-zero; stop and report it.
3. Call `create_attachment_from_upload(issue, assetUrl, title?, subtitle?)`.

To embed instead, use `prepare_file_upload(filename, contentType, size)` and the same upload, skip step 3,
and put the returned markdown or `assetUrl` in the body of `save_comment`, `save_document` or the issue
description.

## Pitfalls

- Send every header in `uploadRequest.headers` verbatim, with the same names, casing and values. A missing
  or changed header returns HTTP 403.
- The signed URL expires after 60 seconds. Prepare, PUT and finalize one file before starting the next.
- Never base64-encode the file or pass its bytes through model text.
- Do not also add `assetUrl` to the issue's links in `save_issue`.

## Tiny Files Without A Shell

`create_attachment` is a deprecated base64 fallback for tiny files, for when no shell or HTTP is
available. Produce the base64 and the sha256 with a command, never by writing them out yourself.

## Remove A Link

`delete_attachment` removes a Resources link; take the attachment ID from `get_issue`. The tools cannot edit
or permanently delete the stored file.

## Who Can

- Agents on the full Linear MCP server can attach and embed.
- Omnigent runner sessions on the guarded Linear server cannot upload (reads and comments only). Put the
  evidence on the PR instead: PNGs on a `pr-assets/<task-branch>` branch, linked by blob URL in a PR
  comment, then link the PR from a Linear comment.
- Read-only agents cannot.

Without upload access, say so and name where the evidence lives; do not conclude that attachments are
impossible.
