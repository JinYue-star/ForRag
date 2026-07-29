$ErrorActionPreference = 'Stop'
$p = (Get-ChildItem -Path 'C:\Users\10603\Desktop\*\*\_parts\10_conclusion.md').FullName
$l = Get-Content -LiteralPath $p -Encoding UTF8
function wc($a) { (($a -join ' ') -split '\s+' | Where-Object { $_ -ne '' }).Count }
Write-Output ("TOTAL " + (wc $l))
$refStart = -1; $refEnd = -1
for ($i = 0; $i -lt $l.Count; $i++) {
    if ($l[$i] -eq '# References') { $refStart = $i }
    if ($l[$i] -like '# Appendix A*') { $refEnd = $i - 1 }
}
Write-Output ("REFS lines " + ($refStart + 1) + " to " + ($refEnd + 1))
$refs = ($l[$refStart..$refEnd] -join "`n")
$bytes = [System.Text.Encoding]::UTF8.GetBytes($refs)
$sha = [System.Security.Cryptography.SHA256]::Create()
Write-Output ("REFHASH " + (($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') }) -join ''))
Write-Output ("REFWORDS " + (wc $l[$refStart..$refEnd]))
Write-Output ("CH10 " + (wc $l[0..($refStart - 1)]))
Write-Output ("APPX " + (wc $l[($refEnd + 1)..($l.Count - 1)]))
