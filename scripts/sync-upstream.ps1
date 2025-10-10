# Sync with upstream repository
# Usage: .\scripts\sync-upstream.ps1

Write-Host "🔄 Syncing with upstream repository..." -ForegroundColor Cyan

# Fetch latest changes from upstream
Write-Host "📥 Fetching upstream changes..."
git fetch upstream

# Switch to main branch
Write-Host "🔄 Switching to main branch..."
git checkout main

# Merge upstream changes
Write-Host "🔗 Merging upstream/main into main..."
git merge upstream/main

# Push to your fork
Write-Host "📤 Pushing updates to your fork..."
git push origin main

Write-Host "✅ Sync complete!" -ForegroundColor Green
Write-Host "Your fork is now up to date with the upstream repository." -ForegroundColor Green