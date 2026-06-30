#!/usr/bin/env pwsh
# Optional pre-commit guard for PR review artifact isolation.

$staged = @(git diff --cached --name-only)
$review = @($staged | Where-Object { $_ -like '.documentation/specs/pr-review/pr-*.md' })
$other = @($staged | Where-Object { $_ -notlike '.documentation/specs/pr-review/pr-*.md' })

if ($review.Count -gt 0 -and $other.Count -gt 0) {
    Write-Error "DevSpark: PR review files must be committed in isolation. Split this commit."
    Write-Error "Review files staged:  $($review -join ', ')"
    Write-Error "Other files staged:   $($other -join ', ')"
    exit 1
}
