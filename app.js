/**
 * Technical SEO Workstation Pro - Developer Controller
 * High-density UI rendering, Theme Switcher, log polling, and table interactions.
 */

document.addEventListener("DOMContentLoaded", () => {
  // DOM Handles
  const htmlEl = document.documentElement;
  const themeToggleBtn = document.getElementById("themeToggleBtn");
  const themeIcon = document.getElementById("themeIcon");
  const themeLabel = document.getElementById("themeLabel");

  const auditForm = document.getElementById("auditForm");
  const targetUrlInput = document.getElementById("targetUrlInput");
  const clearInputBtn = document.getElementById("clearInputBtn");
  const submitBtn = document.getElementById("submitBtn");
  const resetDashboardBtn = document.getElementById("resetDashboardBtn");
  const btnIcon = document.getElementById("btnIcon");
  const btnText = document.getElementById("btnText");
  const presetChips = document.querySelectorAll(".preset-chip");

  if (resetDashboardBtn) {
    resetDashboardBtn.addEventListener("click", () => {
      resetDashboard();
      targetUrlInput.value = "";
      clearInputBtn.classList.add("hidden");
      resetDashboardBtn.classList.add("hidden");
      showToast("Ready for a new audit!", "success");
      targetUrlInput.focus();
    });
  }

  const serverStatusText = document.getElementById("serverStatusText");
  const progressSection = document.getElementById("progressSection");
  const progressStatusText = document.getElementById("progressStatusText");
  const progressCountText = document.getElementById("progressCountText");
  const elapsedTimeText = document.getElementById("elapsedTimeText");
  const progressBar = document.getElementById("progressBar");

  const resultsDashboard = document.getElementById("resultsDashboard");
  const scoreNumber = document.getElementById("scoreNumber");
  const scoreGradeBadge = document.getElementById("scoreGradeBadge");
  const scoreProgressCircle = document.getElementById("scoreProgressCircle");

  const statTotalPages = document.getElementById("statTotalPages");
  const statCritical = document.getElementById("statCritical");
  const statWarnings = document.getElementById("statWarnings");
  const statInfo = document.getElementById("statInfo");
  const statClean = document.getElementById("statClean");

  const contentTabsSection = document.getElementById("contentTabsSection");
  const tabButtons = document.querySelectorAll(".tab-btn");
  const tabPanes = document.querySelectorAll(".tab-content");
  const tabIssueCount = document.getElementById("tabIssueCount");

  const tableSearchInput = document.getElementById("tableSearchInput");
  const sevTabs = document.querySelectorAll(".sev-tab");
  const categoryFilterSelect = document.getElementById("categoryFilterSelect");
  const issuesTableBody = document.getElementById("issuesTableBody");
  const tableResultsCount = document.getElementById("tableResultsCount");

  const terminalConsole = document.getElementById("terminalConsole");
  const autoscrollCheck = document.getElementById("autoscrollCheck");
  const copyLogsBtn = document.getElementById("copyLogsBtn");
  const exportCsvBtn = document.getElementById("exportCsvBtn");
  const exportJsonBtn = document.getElementById("exportJsonBtn");

  // State
  let pollInterval = null;
  let timerInterval = null;
  let startTime = 0;
  let parsedIssues = [];
  let currentSeverityFilter = "ALL";
  let currentCategoryFilter = "ALL";
  let lastLogIndex = 0;
  // Job ID of the audit THIS visitor started (per-tab, survives refresh).
  // Different visitors get a fresh screen because their stored ID won't match.
  let activeJobId = sessionStorage.getItem("seo_job_id") || null;

  // ---------------------------------------------------------------------------
  // Theme Toggle Engine (Light / Dark)
  // ---------------------------------------------------------------------------
  const savedTheme = localStorage.getItem("seo_spider_theme") || "light";
  setTheme(savedTheme);

  themeToggleBtn.addEventListener("click", () => {
    const currentTheme = htmlEl.getAttribute("data-theme") || "light";
    const nextTheme = currentTheme === "light" ? "dark" : "light";
    setTheme(nextTheme);
  });

  function setTheme(theme) {
    htmlEl.setAttribute("data-theme", theme);
    localStorage.setItem("seo_spider_theme", theme);

    if (theme === "dark") {
      themeIcon.className = "fa-solid fa-sun";
      themeLabel.innerText = "Light Mode";
    } else {
      themeIcon.className = "fa-solid fa-moon";
      themeLabel.innerText = "Dark Mode";
    }
  }

  // ---------------------------------------------------------------------------
  // Address Bar & Preset Controls
  // ---------------------------------------------------------------------------
  targetUrlInput.addEventListener("input", () => {
    clearInputBtn.classList.toggle("hidden", targetUrlInput.value.trim().length === 0);
  });

  clearInputBtn.addEventListener("click", () => {
    targetUrlInput.value = "";
    clearInputBtn.classList.add("hidden");
    targetUrlInput.focus();
  });

  presetChips.forEach(chip => {
    chip.addEventListener("click", () => {
      let val = chip.dataset.url;
      if (val.startsWith("https://")) val = val.replace("https://", "");
      if (val.startsWith("http://")) val = val.replace("http://", "");
      targetUrlInput.value = val;
      clearInputBtn.classList.remove("hidden");
      targetUrlInput.focus();
    });
  });

  // ---------------------------------------------------------------------------
  // Tab Navigation
  // ---------------------------------------------------------------------------
  tabButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      const targetTab = btn.dataset.tab;
      tabButtons.forEach(b => b.classList.remove("active"));
      tabPanes.forEach(p => p.classList.remove("active"));

      btn.classList.add("active");
      document.getElementById(targetTab).classList.add("active");
    });
  });

  // ---------------------------------------------------------------------------
  // Form Submit Execution
  // ---------------------------------------------------------------------------
  auditForm.addEventListener("submit", async (e) => {
    e.preventDefault();

    let url = targetUrlInput.value.trim();
    if (!url) return;

    if (!url.startsWith("http://") && !url.startsWith("https://")) {
      url = "https://" + url;
    }

    setUiLoading(true);
    resetDashboard();

    try {
      const response = await fetch("/api/audit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url })
      });

      const data = await response.json();

      if (response.ok) {
        if (data.job_id) {
          activeJobId = data.job_id;
          sessionStorage.setItem("seo_job_id", activeJobId);
        }
        showToast("🚀 Scan initiated for " + url, "success");
        startTimer();
        startPolling();
      } else {
        showToast("⚠️ " + (data.error || "Failed to launch scanner"), "error");
        setUiLoading(false);
      }
    } catch (err) {
      showToast("❌ Unable to connect to app server backend", "error");
      setUiLoading(false);
    }
  });

  // ---------------------------------------------------------------------------
  // Polling Loop
  // ---------------------------------------------------------------------------
  function startPolling() {
    if (pollInterval) clearInterval(pollInterval);
    lastLogIndex = 0;
    pollInterval = setInterval(fetchStatus, 500);
  }

  function stopPolling() {
    if (pollInterval) {
      clearInterval(pollInterval);
      pollInterval = null;
    }
    stopTimer();
  }

  async function fetchStatus() {
    try {
      const resp = await fetch("/api/status");
      if (!resp.ok) return;

      const data = await resp.json();

      // If the current server job was NOT started by this visitor,
      // ignore it — don't leak another person's results onto this screen.
      if (data.job_id && activeJobId && data.job_id !== activeJobId) {
        return;
      }

      updateDashboardFromJob(data);

      if (data.status === "completed" || data.status === "error") {
        stopPolling();
        setUiLoading(false);

        if (data.status === "completed") {
          showToast("✅ Audit matrix generated successfully!", "success");
        } else {
          showToast("❌ Audit failed: " + (data.error_message || "Error"), "error");
        }
      }
    } catch (err) {
      console.error("Poll status error:", err);
    }
  }

  // ---------------------------------------------------------------------------
  // Dashboard & Parser Update
  // ---------------------------------------------------------------------------
  function updateDashboardFromJob(job) {
    if (job.sheet_url) {
      if (openGoogleSheetBtn) openGoogleSheetBtn.href = job.sheet_url;
    } else if (job.url) {
      updateSheetButtonHref(job.url);
    }

    if (job.logs && job.logs.length > lastLogIndex) {
      const newLogs = job.logs.slice(lastLogIndex);
      newLogs.forEach(line => appendTerminalLine(line));
      lastLogIndex = job.logs.length;

      parseLogStream(job.logs);
    }

    const total = job.total_pages || 0;
    const current = job.audited_count || 0;

    if (job.status === "running" || job.status === "completed") {
      progressSection.classList.remove("hidden");
      
      let pct = 0;
      if (total > 0) {
        pct = Math.min(100, Math.round((current / total) * 100));
      }

      progressBar.style.width = pct + "%";
      progressCountText.innerText = `${current} / ${total || "?"} pages`;

      if (job.status === "completed") {
        progressBar.style.width = "100%";
        progressStatusText.innerHTML = '<i class="fa-solid fa-circle-check"></i> Audit Completed';
        if (resetDashboardBtn) resetDashboardBtn.classList.remove("hidden");
      } else {
        progressStatusText.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Crawling and auditing pages (${pct}%)...`;
      }
    }
  }

  function parseLogStream(logs) {
    let score = null;
    let grade = null;
    let totalPages = 0;
    let criticalCount = 0;
    let warningCount = 0;
    let infoCount = 0;
    let cleanPages = 0;

    let inCriticalSection = false;
    let inWarningSection = false;
    let currentCategory = "General";
    let currentIssueTitle = "";

    parsedIssues = [];

    for (let i = 0; i < logs.length; i++) {
      const line = logs[i];

      if (line.includes("FOUND") && line.includes("USER-FACING PAGES TO AUDIT")) {
        const match = line.match(/FOUND\s+(\d+)\s+USER-FACING/i);
        if (match) totalPages = parseInt(match[1]);
      }

      if (line.includes("OVERALL SEO AUDIT SCORE")) {
        const match = line.match(/SCORE\s*:\s*(\d+)\s*\/\s*100\s*(.*)/i);
        if (match) {
          score = parseInt(match[1]);
          grade = match[2].trim();
        }
      }

      if (line.includes("Critical:") && line.includes("Warning:") && line.includes("Clean pages:")) {
        const critMatch = line.match(/Critical:\s*(\d+)/i);
        const warnMatch = line.match(/Warning:\s*(\d+)/i);
        const infoMatch = line.match(/Info:\s*(\d+)/i);
        const cleanMatch = line.match(/Clean pages:\s*(\d+)/i);

        if (critMatch) criticalCount = parseInt(critMatch[1]);
        if (warnMatch) warningCount = parseInt(warnMatch[1]);
        if (infoMatch) infoCount = parseInt(infoMatch[1]);
        if (cleanMatch) cleanPages = parseInt(cleanMatch[1]);
      }

      if (line.includes("🔴 CRITICAL ISSUES")) {
        inCriticalSection = true;
        inWarningSection = false;
      } else if (line.includes("🟡 WARNINGS")) {
        inCriticalSection = false;
        inWarningSection = true;
      }

      if ((inCriticalSection || inWarningSection) && line.trim().startsWith("[")) {
        const issueMatch = line.match(/\[(.*?)\]\s*(.*)/);
        if (issueMatch) {
          currentCategory = issueMatch[1].trim();
          currentIssueTitle = issueMatch[2].trim();

          let issueUrl = "";
          let issueDetail = "";

          for (let j = i + 1; j < Math.min(i + 5, logs.length); j++) {
            const subLine = logs[j].trim();
            if (subLine.startsWith("↳ http")) {
              issueUrl = subLine.replace("↳", "").trim();
            } else if (subLine.startsWith("↳") && !subLine.startsWith("↳ http")) {
              issueDetail = subLine.replace("↳", "").trim();
            }
          }

          if (currentIssueTitle && issueUrl) {
            parsedIssues.push({
              severity: inCriticalSection ? "🔴 Critical" : "🟡 Warning",
              category: currentCategory,
              issue: currentIssueTitle,
              page_url: issueUrl,
              detail: issueDetail || "Review page elements against technical SEO recommendations."
            });
          }
        }
      }
    }

    if (score !== null || parsedIssues.length > 0) {
      resultsDashboard.classList.remove("hidden");
      contentTabsSection.classList.remove("hidden");

      if (score !== null) {
        updateScoreGauge(score, grade);
      }

      statTotalPages.innerText = totalPages || (parsedIssues.length ? new Set(parsedIssues.map(p => p.page_url)).size : 0);
      statCritical.innerText = criticalCount;
      statWarnings.innerText = warningCount;
      statInfo.innerText = infoCount;
      statClean.innerText = cleanPages;

      tabIssueCount.innerText = parsedIssues.length;
      renderIssuesTable();
    }
  }

  // ---------------------------------------------------------------------------
  // Score Gauge Animator
  // ---------------------------------------------------------------------------
  function updateScoreGauge(score, gradeText) {
    scoreNumber.innerText = score;
    
    // SVG Dasharray = 263.89 (r=42)
    const maxDash = 263.89;
    const offset = maxDash - (maxDash * (score / 100));
    scoreProgressCircle.style.strokeDashoffset = offset;

    let strokeColor = "var(--sev-red)";
    let gradeClass = "grade-f";

    if (score >= 90) {
      strokeColor = "var(--sev-green)";
      gradeClass = "grade-a";
    } else if (score >= 75) {
      strokeColor = "var(--sev-yellow)";
      gradeClass = "grade-b";
    } else if (score >= 50) {
      strokeColor = "#f97316";
      gradeClass = "grade-c";
    }

    scoreProgressCircle.style.stroke = strokeColor;
    scoreGradeBadge.className = `grade-pill ${gradeClass}`;
    scoreGradeBadge.innerText = gradeText || (score >= 75 ? "🟡 GOOD" : "🔴 POOR");
  }

  // ---------------------------------------------------------------------------
  // Developer Matrix Table Renderer
  // ---------------------------------------------------------------------------
  function renderIssuesTable() {
    const searchText = tableSearchInput.value.toLowerCase().trim();

    const filtered = parsedIssues.filter(item => {
      if (currentSeverityFilter !== "ALL" && item.severity !== currentSeverityFilter) {
        return false;
      }
      if (currentCategoryFilter !== "ALL" && item.category.toLowerCase() !== currentCategoryFilter.toLowerCase()) {
        return false;
      }
      if (searchText) {
        const matchesUrl = item.page_url.toLowerCase().includes(searchText);
        const matchesCat = item.category.toLowerCase().includes(searchText);
        const matchesIssue = item.issue.toLowerCase().includes(searchText);
        const matchesDetail = item.detail.toLowerCase().includes(searchText);
        return matchesUrl || matchesCat || matchesIssue || matchesDetail;
      }
      return true;
    });

    tableResultsCount.innerText = `Showing ${filtered.length} of ${parsedIssues.length} issues`;

    if (filtered.length === 0) {
      issuesTableBody.innerHTML = `
        <tr>
          <td colspan="5" class="table-placeholder">
            <i class="fa-solid fa-filter-circle-xmark placeholder-icon"></i>
            <p>No audit findings match the selected search or filter criteria.</p>
          </td>
        </tr>
      `;
      return;
    }

    let html = "";
    filtered.forEach(item => {
      let badgeClass = "sev-info";
      if (item.severity.includes("Critical")) badgeClass = "sev-critical";
      if (item.severity.includes("Warning")) badgeClass = "sev-warning";

      html += `
        <tr>
          <td><span class="sev-badge ${badgeClass}">${item.severity}</span></td>
          <td><span class="cat-tag">${escapeHtml(item.category)}</span></td>
          <td class="check-title">${escapeHtml(item.issue)}</td>
          <td>
            <a href="${escapeHtml(item.page_url)}" target="_blank" rel="noopener" class="url-code-link">
              ${escapeHtml(item.page_url)} <i class="fa-solid fa-arrow-up-right-from-square" style="font-size:9px;"></i>
            </a>
          </td>
          <td class="rec-text">${escapeHtml(item.detail)}</td>
        </tr>
      `;
    });

    issuesTableBody.innerHTML = html;
  }

  tableSearchInput.addEventListener("input", renderIssuesTable);

  sevTabs.forEach(tab => {
    tab.addEventListener("click", () => {
      sevTabs.forEach(t => t.classList.remove("active"));
      tab.classList.add("active");
      currentSeverityFilter = tab.dataset.severity;
      renderIssuesTable();
    });
  });

  categoryFilterSelect.addEventListener("change", (e) => {
    currentCategoryFilter = e.target.value;
    renderIssuesTable();
  });

  // ---------------------------------------------------------------------------
  // Terminal Logs Console
  // ---------------------------------------------------------------------------
  function appendTerminalLine(text) {
    const div = document.createElement("div");
    div.className = "log-line";

    if (text.includes("[System]") || text.includes("===")) {
      div.classList.add("system");
    } else if (text.includes("✅") || text.includes("Success")) {
      div.classList.add("success");
    } else if (text.includes("⚠️") || text.includes("WARN")) {
      div.classList.add("warn");
    } else if (text.includes("❌") || text.includes("Critical") || text.includes("Error")) {
      div.classList.add("error");
    }

    div.innerText = text;
    terminalConsole.appendChild(div);

    if (autoscrollCheck.checked) {
      terminalConsole.scrollTop = terminalConsole.scrollHeight;
    }
  }

  copyLogsBtn.addEventListener("click", () => {
    navigator.clipboard.writeText(terminalConsole.innerText).then(() => {
      showToast("📋 Logs copied to clipboard!", "success");
    });
  });

  // ---------------------------------------------------------------------------
  // Export & Google Sheet Functions
  // ---------------------------------------------------------------------------
  // ---------------------------------------------------------------------------
  // Export & Google Sheet Functions
  // ---------------------------------------------------------------------------
  const openGoogleSheetBtn = document.getElementById("openGoogleSheetBtn");

  function getGoogleSheetUrlForDomain(rawUrl) {
    if (!rawUrl) return "https://docs.google.com/spreadsheets";
    let domain = rawUrl.trim();
    try {
      if (domain.startsWith("http://") || domain.startsWith("https://")) {
        domain = new URL(domain).hostname;
      }
    } catch (e) {}
    domain = domain.replace(/^www\./i, "").split("/")[0].split(":")[0];
    if (!domain) return "https://docs.google.com/spreadsheets";
    
    return `https://drive.google.com/drive/search?q=SEO_Report_${encodeURIComponent(domain)}`;
  }

  function updateSheetButtonHref(domainOrUrl) {
    if (!openGoogleSheetBtn) return;
    const urlToUse = domainOrUrl || targetUrlInput.value.trim();
    openGoogleSheetBtn.href = getGoogleSheetUrlForDomain(urlToUse);
  }

  if (openGoogleSheetBtn) {
    updateSheetButtonHref();

    targetUrlInput.addEventListener("input", () => {
      updateSheetButtonHref();
    });

    openGoogleSheetBtn.addEventListener("click", () => {
      updateSheetButtonHref();
    });
  }

  exportCsvBtn.addEventListener("click", () => {
    if (parsedIssues.length === 0) {
      showToast("⚠️ No issues available for export", "error");
      return;
    }

    let csv = "Severity,Category,Issue,Page URL,Recommendation\n";
    parsedIssues.forEach(item => {
      const row = [
        `"${item.severity.replace(/"/g, '""')}"`,
        `"${item.category.replace(/"/g, '""')}"`,
        `"${item.issue.replace(/"/g, '""')}"`,
        `"${item.page_url.replace(/"/g, '""')}"`,
        `"${item.detail.replace(/"/g, '""')}"`
      ];
      csv += row.join(",") + "\n";
    });

    downloadBlob(csv, "text/csv;charset=utf-8;", "seo_audit_issues.csv");
    showToast("💾 Exported audit matrix to CSV!", "success");
  });

  exportJsonBtn.addEventListener("click", () => {
    if (parsedIssues.length === 0) {
      showToast("⚠️ No issues available for export", "error");
      return;
    }

    const jsonStr = JSON.stringify(parsedIssues, null, 2);
    downloadBlob(jsonStr, "application/json;charset=utf-8;", "seo_audit_issues.json");
    showToast("💾 Exported audit matrix to JSON!", "success");
  });

  function downloadBlob(content, type, filename) {
    const blob = new Blob([content], { type });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  // ---------------------------------------------------------------------------
  // Helper Utils
  // ---------------------------------------------------------------------------
  function setUiLoading(isLoading) {
    submitBtn.disabled = isLoading;
    if (isLoading) {
      btnIcon.className = "fa-solid fa-spinner fa-spin";
      btnText.innerText = "Running Scan...";
      serverStatusText.innerText = "Scanner Active";
    } else {
      btnIcon.className = "fa-solid fa-play";
      btnText.innerText = "Run Crawl & Audit";
      serverStatusText.innerText = "Engine Ready";
    }
  }

  function startTimer() {
    startTime = Date.now();
    if (timerInterval) clearInterval(timerInterval);
    timerInterval = setInterval(() => {
      const elapsedSec = Math.floor((Date.now() - startTime) / 1000);
      const mins = String(Math.floor(elapsedSec / 60)).padStart(2, '0');
      const secs = String(elapsedSec % 60).padStart(2, '0');
      elapsedTimeText.innerText = `${mins}:${secs}`;
    }, 1000);
  }

  function stopTimer() {
    if (timerInterval) {
      clearInterval(timerInterval);
      timerInterval = null;
    }
  }

  function resetDashboard() {
    lastLogIndex = 0;
    parsedIssues = [];
    activeJobId = null;
    sessionStorage.removeItem("seo_job_id");
    terminalConsole.innerHTML = "";
    progressBar.style.width = "0%";
    progressCountText.innerText = "0 / 0 pages";
    elapsedTimeText.innerText = "00:00";

    resultsDashboard.classList.add("hidden");
    contentTabsSection.classList.add("hidden");
    progressSection.classList.add("hidden");
    if (resetDashboardBtn) resetDashboardBtn.classList.add("hidden");
  }

  // ---------------------------------------------------------------------------
  // Health Score Modal & Analytics Controller
  // ---------------------------------------------------------------------------
  const healthScoreCard = document.getElementById("healthScoreCard");
  const scoreModal = document.getElementById("scoreModal");
  const closeScoreModalBtn = document.getElementById("closeScoreModalBtn");
  const modalDismissBtn = document.getElementById("modalDismissBtn");

  const modalScoreVal = document.getElementById("modalScoreVal");
  const modalScoreGrade = document.getElementById("modalScoreGrade");
  const modalCleanRatio = document.getElementById("modalCleanRatio");
  const modalCleanPagesSub = document.getElementById("modalCleanPagesSub");
  const modalPenaltyVal = document.getElementById("modalPenaltyVal");
  const modalPenaltyAvg = document.getElementById("modalPenaltyAvg");
  const modalIssuesRatio = document.getElementById("modalIssuesRatio");

  const modalCleanPercentText = document.getElementById("modalCleanPercentText");
  const modalBarClean = document.getElementById("modalBarClean");
  const modalBarAffected = document.getElementById("modalBarAffected");
  const modalBarCritical = document.getElementById("modalBarCritical");
  const modalBarWarning = document.getElementById("modalBarWarning");
  const modalBarInfo = document.getElementById("modalBarInfo");

  const modalLegendClean = document.getElementById("modalLegendClean");
  const modalLegendAffected = document.getElementById("modalLegendAffected");
  const modalLegendCritical = document.getElementById("modalLegendCritical");
  const modalLegendWarning = document.getElementById("modalLegendWarning");
  const modalLegendInfo = document.getElementById("modalLegendInfo");

  if (healthScoreCard) {
    healthScoreCard.addEventListener("click", openScoreModal);
  }

  if (closeScoreModalBtn) closeScoreModalBtn.addEventListener("click", closeScoreModal);
  if (modalDismissBtn) modalDismissBtn.addEventListener("click", closeScoreModal);

  if (scoreModal) {
    scoreModal.addEventListener("click", (e) => {
      if (e.target === scoreModal) closeScoreModal();
    });
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && scoreModal && !scoreModal.classList.contains("hidden")) {
      closeScoreModal();
    }
  });

  function openScoreModal() {
    if (!scoreModal) return;

    const currentScore = parseInt(scoreNumber.innerText) || 0;
    const totalPages = parseInt(statTotalPages.innerText) || 0;
    const criticalCount = parseInt(statCritical.innerText) || 0;
    const warningCount = parseInt(statWarnings.innerText) || 0;
    const infoCount = parseInt(statInfo.innerText) || 0;
    const cleanPages = parseInt(statClean.innerText) || 0;
    const totalIssues = parsedIssues.length || (criticalCount + warningCount + infoCount);
    const affectedPages = Math.max(0, totalPages - cleanPages);

    // Calculate Penalty Score & Ratios
    const totalPenalty = (criticalCount * 5.0) + (warningCount * 2.0) + (infoCount * 0.5);
    const avgPenalty = totalPages > 0 ? (totalPenalty / totalPages).toFixed(1) : "0.0";
    const issuesPerPage = totalPages > 0 ? (totalIssues / totalPages).toFixed(1) : "0.0";
    const cleanPct = totalPages > 0 ? Math.round((cleanPages / totalPages) * 100) : 0;
    const affectedPct = 100 - cleanPct;

    // Severity weighting ratio
    const criticalWeight = criticalCount * 5.0;
    const warningWeight = warningCount * 2.0;
    const infoWeight = infoCount * 0.5;
    const sumWeight = totalPenalty > 0 ? totalPenalty : 1;

    const critBarPct = Math.round((criticalWeight / sumWeight) * 100);
    const warnBarPct = Math.round((warningWeight / sumWeight) * 100);
    const infoBarPct = 100 - (critBarPct + warnBarPct);

    // Update KPI Card Displays
    modalScoreVal.innerText = currentScore;
    let gradeBadgeText = "🔴 POOR (F)";
    let gradeClass = "grade-f";

    if (currentScore >= 90) { gradeBadgeText = "🟢 EXCELLENT (A)"; gradeClass = "grade-a"; }
    else if (currentScore >= 75) { gradeBadgeText = "🟡 GOOD (B)"; gradeClass = "grade-b"; }
    else if (currentScore >= 50) { gradeBadgeText = "🟠 NEEDS WORK (C)"; gradeClass = "grade-c"; }

    modalScoreGrade.className = `grade-pill ${gradeClass}`;
    modalScoreGrade.innerText = gradeBadgeText;

    modalCleanRatio.innerText = `${cleanPct}%`;
    modalCleanPagesSub.innerText = `${cleanPages} / ${totalPages} pages`;

    modalPenaltyVal.innerText = Math.round(totalPenalty);
    modalPenaltyAvg.innerText = `${avgPenalty} pts / page`;
    modalIssuesRatio.innerText = issuesPerPage;

    // Update Graph Bars
    modalCleanPercentText.innerText = `${cleanPct}% Clean (${cleanPages}/${totalPages} pages)`;
    modalBarClean.style.width = `${cleanPct}%`;
    modalBarAffected.style.width = `${affectedPct}%`;

    modalLegendClean.innerText = `${cleanPages} pages (${cleanPct}%)`;
    modalLegendAffected.innerText = `${affectedPages} pages (${affectedPct}%)`;

    modalBarCritical.style.width = `${critBarPct}%`;
    modalBarWarning.style.width = `${warnBarPct}%`;
    modalBarInfo.style.width = `${Math.max(0, infoBarPct)}%`;

    modalLegendCritical.innerText = `${criticalCount} issues (${criticalWeight.toFixed(1)} pts)`;
    modalLegendWarning.innerText = `${warningCount} issues (${warningWeight.toFixed(1)} pts)`;
    modalLegendInfo.innerText = `${infoCount} notices (${infoWeight.toFixed(1)} pts)`;

    scoreModal.classList.remove("hidden");
  }

  function closeScoreModal() {
    if (scoreModal) scoreModal.classList.add("hidden");
  }

  function showToast(msg, type = "success") {
    const container = document.getElementById("toastContainer");
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.innerHTML = `<i class="fa-solid ${type === 'success' ? 'fa-circle-check' : 'fa-circle-exclamation'}"></i> <span>${msg}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = "0";
      setTimeout(() => toast.remove(), 200);
    }, 3000);
  }

  function escapeHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  fetchStatus();
});
