export function applicationReturnInstructions(returnURL: string, fileImport = true) {
  return `CREATE THE RETURN LINK:
Run this recipe internally with your completed object named application_package. Compression is required. Never guess the encoding, fabricate a token, use a URL shortener or remove part of the data. Do not browse, fetch or visit the generated return link; only I should open it. The data belongs after #applicationz=, never in query parameters. Keep the exact return_url below.
RETURN_LINK_RECIPE_BEGIN
import base64, json, zlib
return_url = ${JSON.stringify(returnURL)}
payload = json.dumps(application_package, ensure_ascii=False, separators=(",", ":"))
token = base64.urlsafe_b64encode(zlib.compress(payload.encode("utf-8"), 9)).decode("ascii").rstrip("=")
link = return_url + "#applicationz=" + token
if len(link) <= 24000 and len(payload.encode("utf-8")) <= 200000:
    print("[Return to Radius with my documents](" + link + ")")
else:
    print("RETURN_FILE_REQUIRED")
RETURN_LINK_RECIPE_END
If the recipe reports RETURN_FILE_REQUIRED, or you cannot generate a verified link, offer the completed object as a downloadable application-package.json file instead, with one sentence: "${fileImport ? 'Download this file, then choose Import result file in Radius.' : 'Download this file, then paste its contents into Returned application package in Radius.'}" Do not show the JSON contents. Only if both code execution and downloadable files are unavailable, explain the limitation briefly and give one fenced json block as a manual fallback. Never give an empty or unverified return link. Never claim the documents have already been sent to Radius or the application submitted. Radius uses the returned LaTeX source for LaTeX downloads and retains the original DOCX workflow for Word exports. A return link is a snapshot, not a live Canvas share link.
`
}
