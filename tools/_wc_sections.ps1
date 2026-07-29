param([string]$Path)
$lines = Get-Content $Path -Encoding UTF8
$sec = 'HEAD'
$counts = [ordered]@{}
foreach ($l in $lines) {
  if ($l -match '^##\s') { $sec = $l.Trim() }
  if (-not $counts.Contains($sec)) { $counts[$sec] = 0 }
  $counts[$sec] += (($l -split '\s+') | Where-Object { $_ -ne '' }).Count
}
$total = 0
foreach ($k in $counts.Keys) { '{0,5}  {1}' -f $counts[$k], $k; $total += $counts[$k] }
'{0,5}  TOTAL' -f $total
