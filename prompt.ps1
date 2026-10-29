<#
.SYNOPSIS
  Makes the prompts for an image model (gpt-image, Flux, SDXL, ...) that produce good reference images for this kit.
.DESCRIPTION
  1. The FRONT prompt: ONE figure, front view, A-pose. This picture is the one that goes into generate.cmd.
  2. Then it asks whether you also want the sides and the back. If yes, a SECOND prompt is made: the same character
     from all four directions (front, right, back, left) in one picture, as four separate figures in a row. Give the
     front picture to the image model as the reference when you use it, so that the character stays identical.
     (The four-view picture is NOT what you drop on generate.cmd: it is the reference for the back and the sides.)
  3. Then it asks whether you also want TEXTURE prompts to finish the model with AI edits (ai.cmd, "put THIS on her
     face"): a FACE picture (a close-up with the hair kept completely off the face, so no strand gets projected onto
     the cheeks), a HAIR picture (the head from behind: color, parting, how it falls) and a MATERIALS sheet (seamless
     swatches of the outfit's materials). These are not for generate.cmd either; they are what you give the AI agent.
  The prompts go to the screen and to out\prompt-<kind>.txt, -4views.txt, -face.txt, -hair.txt, -materials.txt; the one
  you choose with -View (default: front) also goes to the clipboard.
.EXAMPLE
  .\prompt.ps1 -Description "tall man, green field jacket, grey shirt, dark jeans, short blond hair, 2001"
  .\prompt.ps1 -Kind object -Description "wooden barrel with iron hoops"
  .\prompt.ps1 -Description "..." -FourViews          # also the four-direction prompt, without asking
  .\prompt.ps1 -Description "..." -View 4views        # that prompt on the clipboard
  .\prompt.ps1                                        # asks for everything
#>
param(
    [ValidateSet("character", "creature", "object")][string]$Kind,
    [string]$Description,
    [string]$Style = "PS1 / PS2 era game",
    [ValidateSet("front", "4views", "face", "hair", "materials")][string]$View = "front",
    [switch]$FourViews,
    [switch]$Textures,
    [switch]$NoAsk,
    [switch]$NoClipboard
)

. (Join-Path $PSScriptRoot "scripts\lib.ps1")
$interactive = Test-Interactive -NoAsk:$NoAsk

if (-not $Kind) {
    $k = Read-Host "Kind: character / creature / object [character]"
    $Kind = if ($k) { $k } else { "character" }
    if ($Kind -notin "character", "creature", "object") { throw "Unknown kind: $Kind" }
}
if (-not $Description) {
    $Description = Read-Host "Describe it (outfit, build, hair, colors, era - or the object)"
}
$what = if ($Kind -eq "creature") { "creature" } else { "character" }

$common = @"
Neutral soft diffuse studio light from all sides, no cast shadows, no strong highlights, true flat colors (it becomes
the texture - no baked dramatic lighting).
Plain smooth single-color background: transparent, or pure magenta #FF00FF if transparency is not available.
No scene, no floor, no text, no logo, no watermark.
"@

$prompts = [ordered]@{}
if ($Kind -eq "object") {
    $prompts["front"] = @"
Reference image for turning ONE object into a 3D game model: $Description.
A single object, entirely visible with a small margin, centered.
Three-quarter view from slightly above (about 30 degrees to the side, 20 degrees from above), almost orthographic, no
perspective distortion.
$common
Style: $Style prop - simple readable forms, hand-painted texture look, crisp materials and seams.

Negative (if the model takes one): cropped, cut off, multiple objects, scene, floor, cast shadow, text, logo,
watermark, strong perspective, dramatic lighting, strong highlights.
"@
} else {
    $apose = @"
MANDATORY A-pose: standing upright, both arms straight and angled about 45 degrees down and out from the shoulders,
hands open with palms facing the thighs, a clear gap of background between each arm and the torso and between the
hands and the hips; legs straight, feet a little apart with a gap between the legs; nothing in the hands, nothing
covering the body.
"@
    $prompts["front"] = @"
Reference image for turning ONE $what into a 3D game model: $Description.
A single figure, full body from the top of the head to the soles, entirely visible with a small margin, centered.
Straight front view at mid height, almost orthographic, no perspective distortion.
$apose
$common
Style: $Style $what - clear silhouette, simple readable forms, hand-painted texture look, crisp clothing colors and
seams.

Negative (if the model takes one): cropped, cut off feet, cut off head, multiple characters, turnaround sheet, side
view, three-quarter view, dynamic pose, arms at the sides, arms touching the body, hands on hips, hands in pockets,
T-pose, holding a weapon, props covering the body, scene, floor, cast shadow, text, logo, watermark, strong
perspective, dramatic lighting.
"@

    # the second question: the sides and the back
    $want = [bool]$FourViews
    if (-not $want -and $interactive) {
        Write-Host ""
        $want = (Read-Host "Do you also want the side views and the back view (a second prompt: the same character from all 4 directions)? [y/N]").Trim().ToLower() -in "y", "yes"
    }
    if ($want) {
        $prompts["4views"] = @"
Reference sheet of the SAME $what as in the attached front picture - use that picture as the reference (image-to-image /
edit mode): $Description.
ONE picture with exactly FOUR separate full-body figures standing side by side in one row, left to right: 1 FRONT view,
2 RIGHT SIDE view (strict profile, facing the right edge of the picture), 3 BACK view (seen exactly from behind),
4 LEFT SIDE view (strict profile, facing the left edge of the picture).
Identical character in all four: the same face, hair, clothes, colors, proportions and body size, drawn in the same
style as the front picture.
The same A-pose in every view: both arms straight and angled about 45 degrees down and out (seen edge-on in the side
views), a clear gap between each arm and the torso, legs straight with feet a little apart, nothing in the hands,
nothing covering the body.
Layout: four equal-width columns, each figure centered in its column with a clear empty gap between neighbors, so the
figures never touch or overlap; all four on the same ground line, the same size and the same margin, each complete from
the top of the head to the soles. Straight orthographic views at mid height, no perspective distortion.
$common
No labels, captions, arrows, numbers, grid lines or frames.
Style: $Style $what - clear silhouette, simple readable forms, hand-painted texture look, crisp clothing colors and
seams.

Negative (if the model takes one): a different outfit, hairstyle or face between the views, three-quarter views,
dynamic poses, arms touching the body, hands on hips, T-pose, holding a weapon, perspective, figures touching or
overlapping, more or fewer than four figures, cropped, cut off feet, cut off head, labels, text, numbers, grid, frames,
scene, floor, cast shadow, logo, watermark, dramatic lighting.
"@
    }
}

# the third question: texture pictures for the AI edits
$want = [bool]$Textures
if (-not $want -and $interactive) {
    Write-Host ""
    $want = (Read-Host "Do you also want texture prompts for the face, the hair and the materials (to finish the model with AI edits)? [y/N]").Trim().ToLower() -in "y", "yes"
}
if ($want) {
    $thing = if ($Kind -eq "object") { "object" } else { $what }
    $same = "It is the SAME $thing as in the attached front picture - use that picture as the reference (image-to-image / edit mode)."
    if ($Kind -ne "object") {
        $prompts["face"] = @"
Face texture reference for a 3D game model: the face of $Description.
$same
Close-up of the head seen straight from the front, almost orthographic (no perspective distortion), the head filling the
picture with a small margin, from the top of the head to the base of the neck, both ears visible.
The hair is pulled back or tied up and kept completely OFF the face: no strands over the forehead, the cheeks, the eyes
or the ears, no bangs, no hat, no glasses, no jewelry on the face.
Neutral relaxed expression, eyes open and looking straight ahead, mouth closed.
Even, flat, soft light from the front and both sides: no cast shadows, no shadow under the nose, the chin or the
hair, no strong highlights, true skin tones, no heavy makeup shadows painted in.
Plain smooth single-color background: transparent, or pure magenta #FF00FF if transparency is not available. No text,
no logo, no watermark.
Style: $Style $what - crisp skin and eye detail, hand-painted texture look.

Negative (if the model takes one): hair over the face, bangs, loose strands, hat, glasses, shadows on the face,
highlights, side view, three-quarter view, tilted head, open mouth, smile, closed eyes, perspective, dramatic lighting,
text, logo, watermark.
"@
        $prompts["hair"] = @"
Hair texture reference for a 3D game model: the hair of $Description.
$same
The head seen exactly from behind, centered, the hair filling most of the picture, with a small margin; the face is
not visible. Show the true color, the parting, the direction of the strands, the length and how it falls (loose, ponytail,
bun, braids), exactly as in the front picture.
Even, flat, soft light from all sides: no cast shadows, no strong highlights, true colors.
Plain smooth single-color background: transparent, or pure magenta #FF00FF if transparency is not available. No text,
no logo, no watermark.
Style: $Style $what - clear strand groups, simple readable forms, hand-painted texture look.

Negative (if the model takes one): face visible, side view, front view, hat, hair accessories hiding the hair, shadows,
highlights, perspective, dramatic lighting, text, logo, watermark.
"@
    }
    $prompts["materials"] = @"
Material texture sheet for a 3D game model of $Description.
ONE flat sheet of seamless, tileable texture swatches, one swatch for each main material of $(if ($Kind -eq 'object') { 'the object' } else { 'the outfit and the body (for example the fabrics, leather, denim, metal and the skin)' }), in a grid of three columns.
Each swatch is a square of the material seen straight on, filling its square edge to edge, with a thin plain gap
between the squares. No objects, no folds or seams crossing the edges, no perspective.
Even, flat, soft light: no cast shadows, no highlights, true colors, the same hand-painted texture look as the front
picture.
$same
No labels, captions, numbers, borders or text.
Style: $Style, hand-painted texture look, crisp detail.

Negative (if the model takes one): objects, clothes on a body, folds, shadows, highlights, perspective, text, labels,
numbers, borders, logo, watermark, dramatic lighting.
"@
}

# one line per paragraph for image models that do not like hard wraps
$flat = { param($t) $t -replace "(?<!\n)\r?\n(?!\r?\n)(?![A-Z][a-z]+ \(|Negative|MANDATORY)", " " }
$out = Join-Path $PSScriptRoot "out"
New-Item -ItemType Directory -Force $out | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding($false)
$files = @()
foreach ($v in @($prompts.Keys)) {
    $prompts[$v] = & $flat $prompts[$v]
    $f = Join-Path $out ("prompt-$Kind" + $(if ($v -eq "front") { "" } else { "-$v" }) + ".txt")
    [IO.File]::WriteAllText($f, $prompts[$v], $utf8)
    $files += $f
}
$pick = if ($prompts.Contains($View)) { $View } else { "front" }
if (-not $NoClipboard) { Set-Clipboard -Value $prompts[$pick] }

foreach ($v in $prompts.Keys) {
    Write-Host ""
    $title = switch ($v) {
        "front"     { "FRONT (the picture for generate.cmd)" }
        "4views"    { "FOUR DIRECTIONS (reference for the back and the sides)" }
        "face"      { "FACE (for the AI edits: the hair is kept off the face)" }
        "hair"      { "HAIR (for the AI edits)" }
        "materials" { "MATERIALS (for the AI edits)" }
    }
    Write-Host ("=== {0} ===" -f $title) -ForegroundColor Cyan
    Write-Host $prompts[$v]
}
Write-Host ""
Write-Host ("Saved: " + ($files -join ", ") + $(if (-not $NoClipboard) { "  ($pick prompt copied to the clipboard)" })) -ForegroundColor Green
if ($prompts.Contains("4views") -or $prompts.Contains("face") -or $prompts.Contains("materials")) {
    Write-Host "Make the FRONT picture first; give it to the image model as the reference for all the others." -ForegroundColor Green
    Write-Host "generate.cmd uses only the front picture. The others are for the AI edits (ai.cmd): give them to the agent as" -ForegroundColor Yellow
    Write-Host "file paths ('put this face picture on the head'); the four-direction picture is not wired into the pipeline yet." -ForegroundColor Yellow
} else {
    Write-Host "Then drag the finished front image onto generate.cmd." -ForegroundColor Green
}
