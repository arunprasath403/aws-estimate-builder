# Deploy the backend stack and the Amplify-hosted web page.
# Usage: .\scripts\deploy.ps1 -BudgetEmail you@example.com [-Profile default] [-Region us-east-1]
param(
    [Parameter(Mandatory = $true)][string]$BudgetEmail,
    [string]$Profile = "default",
    [string]$Region = "us-east-1",
    [string]$Stack = "aws-estimate-builder",
    [string]$AppName = "aws-estimate-builder"
)
# Native commands are checked with Check(); "Stop" would turn harmless stderr output into errors in PowerShell 5.1.
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root ".venv\Scripts\python.exe"
$aws = @("--profile", $Profile, "--region", $Region)

function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

$account = aws sts get-caller-identity @aws --query Account --output text; Check "get-caller-identity"
$bucket = "estimate-builder-artifacts-$account-$Region"

# 1. Renderer dependencies and build
Push-Location (Join-Path $root "renderer"); npm ci --omit=dev --no-audit --no-fund --loglevel=error; Check "npm ci"; Pop-Location
$hashes = ((& $py (Join-Path $root "scripts\build.py") | Select-Object -Last 1).Trim() -split " "); Check "build"
$backendKey = "backend/$($hashes[0]).zip"
$rendererKey = "renderer/$($hashes[1]).zip"

# 2. Upload code (private bucket, created once)
aws s3api head-bucket --bucket $bucket @aws 2>$null
if ($LASTEXITCODE -ne 0) {
    aws s3api create-bucket --bucket $bucket @aws | Out-Null; Check "create-bucket"
    aws s3api put-public-access-block --bucket $bucket @aws --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"; Check "public-access-block"
}
aws s3 cp (Join-Path $root "dist\backend.zip") "s3://$bucket/$backendKey" @aws --only-show-errors; Check "upload backend"
aws s3 cp (Join-Path $root "dist\renderer.zip") "s3://$bucket/$rendererKey" @aws --only-show-errors; Check "upload renderer"

# 3. Amplify app (created once) so the API can allow only its origin
$appId = aws amplify list-apps @aws --query "apps[?name=='$AppName'].appId | [0]" --output text; Check "list-apps"
if (-not $appId -or $appId -eq "None") {
    $appId = aws amplify create-app --name $AppName --platform WEB @aws --query app.appId --output text; Check "create-app"
    aws amplify create-branch --app-id $appId --branch-name main @aws | Out-Null; Check "create-branch"
}
$origin = "https://main.$appId.amplifyapp.com"

# 4. Backend stack
aws cloudformation deploy @aws --stack-name $Stack --template-file (Join-Path $root "infra\template.yaml") `
    --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset `
    --parameter-overrides "CodeBucket=$bucket" "BackendKey=$backendKey" "RendererKey=$rendererKey" "AllowedOrigin=$origin" "BudgetEmail=$BudgetEmail"
Check "cloudformation deploy"
$api = aws cloudformation describe-stacks --stack-name $Stack @aws --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text; Check "describe-stacks"

# 5. Web page: point it at the API, zip, upload to Amplify
"window.APP_CONFIG = { apiUrl: `"$api`" };" | Out-File -Encoding utf8 (Join-Path $root "frontend\config.js")
& $py -c "import sys,zipfile,os; sys.path.insert(0, r'$root\scripts'); import build; z=zipfile.ZipFile(r'$root\dist\frontend.zip','w',zipfile.ZIP_DEFLATED); build.zip_dir(z, r'$root\frontend'); z.close()"; Check "zip frontend"
$dep = aws amplify create-deployment --app-id $appId --branch-name main @aws --output json | ConvertFrom-Json; Check "create-deployment"
Invoke-WebRequest -Uri $dep.zipUploadUrl -Method Put -InFile (Join-Path $root "dist\frontend.zip") -ContentType "application/zip" -UseBasicParsing | Out-Null
aws amplify start-deployment --app-id $appId --branch-name main --job-id $dep.jobId @aws | Out-Null; Check "start-deployment"
"window.APP_CONFIG = { apiUrl: `"/api`" };" | Out-File -Encoding utf8 (Join-Path $root "frontend\config.js")  # back to local dev

Write-Host "API:  $api"
Write-Host "App:  $origin"
