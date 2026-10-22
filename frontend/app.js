/**
 * Serverless IoT Telemetry Dashboard - Main Application Logic
 * Fetches data from API Gateway REST API / Mock Server and updates DOM & Chart.js.
 */

(function () {
  'use strict';

  // State Management
  const state = {
    currentDeviceId: '',
    currentRange: '1h',
    autoRefreshActive: true,
    autoRefreshTimer: null,
    chartInstance: null,
  };

  // DOM Elements
  const elements = {
    deviceSelect: document.getElementById('device-select'),
    refreshBtn: document.getElementById('refresh-btn'),
    autoRefreshBtn: document.getElementById('auto-refresh-btn'),
    themeToggle: document.getElementById('theme-toggle'),
    healthIndicator: document.getElementById('health-indicator'),
    healthText: document.getElementById('health-text'),
    errorBanner: document.getElementById('error-banner'),
    errorMessage: document.getElementById('error-message'),
    dismissError: document.getElementById('dismiss-error'),
    valTemperature: document.getElementById('val-temperature'),
    tempTrend: document.getElementById('temp-trend'),
    valHumidity: document.getElementById('val-humidity'),
    humidityTrend: document.getElementById('humidity-trend'),
    valBattery: document.getElementById('val-battery'),
    batteryBar: document.getElementById('battery-bar'),
    batteryPct: document.getElementById('battery-pct'),
    valStatus: document.getElementById('val-status'),
    valLastSeen: document.getElementById('val-last-seen'),
    valFirmware: document.getElementById('val-firmware'),
    rangeButtons: document.querySelectorAll('.range-btn'),
    chartLoading: document.getElementById('chart-loading'),
    chartEmpty: document.getElementById('chart-empty'),
    telemetryChartCanvas: document.getElementById('telemetryChart'),
  };

  // ==========================================================================
  // Initialization
  // ==========================================================================
  function init() {
    initTheme();
    populateDevices();
    initEventListeners();
    initChart();

    // Initial data fetch
    fetchHealth();
    fetchLatestTelemetry();
    fetchTelemetryHistory();

    // Start auto-refresh interval
    startAutoRefresh();
  }

  // ==========================================================================
  // Theme Toggle (Light / Dark)
  // ==========================================================================
  function initTheme() {
    const savedTheme = localStorage.getItem('iot-dashboard-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', savedTheme);
    elements.themeToggle.textContent = savedTheme === 'dark' ? '☀️' : '🌙';
  }

  function toggleTheme() {
    const currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('iot-dashboard-theme', newTheme);
    elements.themeToggle.textContent = newTheme === 'dark' ? '☀️' : '🌙';

    // Update Chart.js grid & label colors on theme change
    if (state.chartInstance) {
      updateChartColors(newTheme);
      state.chartInstance.update();
    }
  }

  // ==========================================================================
  // Device Selection & Controls
  // ==========================================================================
  function populateDevices() {
    elements.deviceSelect.innerHTML = '';
    CONFIG.defaultDevices.forEach((device) => {
      const option = document.createElement('option');
      option.value = device.id;
      option.textContent = device.name;
      elements.deviceSelect.appendChild(option);
    });

    state.currentDeviceId = CONFIG.defaultDevices[0]?.id || 'esp32-sensor-01';
    elements.deviceSelect.value = state.currentDeviceId;
  }

  function initEventListeners() {
    elements.deviceSelect.addEventListener('change', (e) => {
      state.currentDeviceId = e.target.value;
      fetchLatestTelemetry();
      fetchTelemetryHistory();
    });

    elements.refreshBtn.addEventListener('click', () => {
      fetchHealth();
      fetchLatestTelemetry();
      fetchTelemetryHistory();
    });

    elements.autoRefreshBtn.addEventListener('click', toggleAutoRefresh);
    elements.themeToggle.addEventListener('click', toggleTheme);
    elements.dismissError.addEventListener('click', hideError);

    elements.rangeButtons.forEach((btn) => {
      btn.addEventListener('click', (e) => {
        elements.rangeButtons.forEach((b) => b.classList.remove('active'));
        e.target.classList.add('active');
        state.currentRange = e.target.getAttribute('data-range');
        fetchTelemetryHistory();
      });
    });
  }

  function toggleAutoRefresh() {
    state.autoRefreshActive = !state.autoRefreshActive;
    if (state.autoRefreshActive) {
      elements.autoRefreshBtn.classList.add('active');
      elements.autoRefreshBtn.textContent = '● Auto (5s)';
      startAutoRefresh();
    } else {
      elements.autoRefreshBtn.classList.remove('active');
      elements.autoRefreshBtn.textContent = '○ Auto Off';
      stopAutoRefresh();
    }
  }

  function startAutoRefresh() {
    stopAutoRefresh();
    state.autoRefreshTimer = setInterval(() => {
      if (state.autoRefreshActive) {
        fetchLatestTelemetry(true); // silent refresh
        fetchHealth(true);
      }
    }, CONFIG.refreshIntervalMs);
  }

  function stopAutoRefresh() {
    if (state.autoRefreshTimer) {
      clearInterval(state.autoRefreshTimer);
      state.autoRefreshTimer = null;
    }
  }

  // ==========================================================================
  // API Fetching Methods
  // ==========================================================================

  async function fetchHealth(silent = false) {
    try {
      const response = await fetch(`${CONFIG.apiBaseUrl}/health`);
      if (!response.ok) throw new Error(`Health check returned HTTP ${response.status}`);
      const data = await response.json();

      if (data.success && data.data.status === 'HEALTHY') {
        elements.healthIndicator.className = 'health-badge';
        elements.healthText.textContent = 'System Healthy';
      } else {
        throw new Error('Database unreachable');
      }
    } catch (err) {
      elements.healthIndicator.className = 'health-badge unhealthy';
      elements.healthText.textContent = 'System Degraded';
      if (!silent) showError(`Health probe error: ${err.message}`);
    }
  }

  async function fetchLatestTelemetry(silent = false) {
    if (!state.currentDeviceId) return;

    try {
      const url = `${CONFIG.apiBaseUrl}/devices/${encodeURIComponent(state.currentDeviceId)}/latest`;
      const response = await fetch(url);

      if (response.status === 404) {
        // No telemetry recorded yet for this device
        renderEmptyLatestState();
        return;
      }

      if (!response.ok) {
        throw new Error(`API returned HTTP ${response.status}`);
      }

      const json = await response.json();
      if (json.success && json.data) {
        renderLatestData(json.data);
        hideError();
      }
    } catch (err) {
      if (!silent) {
        showError(`Failed to fetch latest telemetry for ${state.currentDeviceId}: ${err.message}`);
      }
    }
  }

  async function fetchTelemetryHistory() {
    if (!state.currentDeviceId) return;

    showChartLoading(true);
    showChartEmpty(false);

    const now = new Date();
    let startTime = new Date();

    switch (state.currentRange) {
      case '1h': startTime.setHours(now.getHours() - 1); break;
      case '6h': startTime.setHours(now.getHours() - 6); break;
      case '24h': startTime.setHours(now.getHours() - 24); break;
      case '7d': startTime.setDate(now.getDate() - 7); break;
      default: startTime.setHours(now.getHours() - 1);
    }

    try {
      const params = new URLSearchParams({
        start: startTime.toISOString(),
        end: now.toISOString(),
        limit: '100',
      });

      const url = `${CONFIG.apiBaseUrl}/devices/${encodeURIComponent(state.currentDeviceId)}/history?${params.toString()}`;
      const response = await fetch(url);

      if (!response.ok) throw new Error(`History query returned HTTP ${response.status}`);

      const json = await response.json();
      const records = json.data || [];

      if (records.length === 0) {
        showChartEmpty(true);
        updateChartData([], [], []);
      } else {
        showChartEmpty(false);
        renderHistoryChart(records);
      }
    } catch (err) {
      showError(`History query failed: ${err.message}`);
      showChartEmpty(true);
    } finally {
      showChartLoading(false);
    }
  }

  // ==========================================================================
  // DOM Renderers
  // ==========================================================================

  function renderLatestData(data) {
    // Temperature
    elements.valTemperature.textContent = Number(data.temperature).toFixed(1);
    elements.tempTrend.textContent = data.temperature > 50 ? '⚠️ High Temp' : 'Normal';
    elements.tempTrend.style.color = data.temperature > 50 ? 'var(--color-critical)' : 'inherit';

    // Humidity
    elements.valHumidity.textContent = Number(data.humidity).toFixed(1);

    // Battery (3.0V cutoff to 4.2V nominal full charge)
    const voltage = Number(data.battery_voltage) || 3.7;
    elements.valBattery.textContent = voltage.toFixed(2);
    const pct = Math.max(0, Math.min(100, Math.round(((voltage - 3.0) / (4.2 - 3.0)) * 100)));
    elements.batteryPct.textContent = `${pct}%`;
    elements.batteryBar.style.width = `${pct}%`;
    elements.batteryBar.style.backgroundColor = pct < 20 ? 'var(--color-critical)' : 'var(--color-battery)';

    // Status Badge
    const status = String(data.status || 'OK').toUpperCase();
    elements.valStatus.textContent = status;
    elements.valStatus.className = 'badge';
    if (status === 'OK') elements.valStatus.classList.add('badge-ok');
    else if (status === 'WARNING') elements.valStatus.classList.add('badge-warning');
    else if (status === 'CRITICAL') elements.valStatus.classList.add('badge-critical');
    else if (status === 'LOW_BATTERY') elements.valStatus.classList.add('badge-low-battery');

    // Last seen timestamp formatting
    if (data.timestamp) {
      const dt = new Date(data.timestamp);
      elements.valLastSeen.textContent = dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    if (data.firmware_version) {
      elements.valFirmware.textContent = `v${data.firmware_version}`;
    }
  }

  function renderEmptyLatestState() {
    elements.valTemperature.textContent = '--.-';
    elements.valHumidity.textContent = '--.-';
    elements.valBattery.textContent = '-.--';
    elements.batteryPct.textContent = '--%';
    elements.batteryBar.style.width = '0%';
    elements.valStatus.textContent = 'NO DATA';
    elements.valStatus.className = 'badge';
    elements.valLastSeen.textContent = 'Never';
  }

  function showError(msg) {
    elements.errorMessage.textContent = msg;
    elements.errorBanner.classList.add('visible');
  }

  function hideError() {
    elements.errorBanner.classList.remove('visible');
  }

  function showChartLoading(show) {
    elements.chartLoading.classList.toggle('visible', show);
  }

  function showChartEmpty(show) {
    elements.chartEmpty.classList.toggle('visible', show);
    elements.telemetryChartCanvas.style.display = show ? 'none' : 'block';
  }

  // ==========================================================================
  // Chart.js Visualization
  // ==========================================================================

  function initChart() {
    if (typeof Chart === 'undefined') {
      console.warn('Chart.js library not loaded; charts disabled.');
      return;
    }

    const ctx = elements.telemetryChartCanvas.getContext('2d');
    const isDark = document.documentElement.getAttribute('data-theme') !== 'light';

    state.chartInstance = new Chart(ctx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [
          {
            label: 'Temperature (°C)',
            data: [],
            borderColor: '#58a6ff',
            backgroundColor: 'rgba(88, 166, 255, 0.1)',
            fill: true,
            tension: 0.3,
            yAxisID: 'yTemp',
            pointRadius: 2,
            pointHoverRadius: 5,
          },
          {
            label: 'Humidity (%)',
            data: [],
            borderColor: '#39d353',
            backgroundColor: 'transparent',
            borderDash: [4, 4],
            tension: 0.3,
            yAxisID: 'yHumidity',
            pointRadius: 2,
            pointHoverRadius: 5,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: 'index',
          intersect: false,
        },
        scales: {
          x: {
            grid: { color: isDark ? '#30363d' : '#e1e4e8' },
            ticks: { color: isDark ? '#8b949e' : '#656d76', maxTicksLimit: 8 },
          },
          yTemp: {
            type: 'linear',
            position: 'left',
            title: { display: true, text: 'Temperature (°C)', color: '#58a6ff' },
            grid: { color: isDark ? '#30363d' : '#e1e4e8' },
            ticks: { color: '#58a6ff' },
          },
          yHumidity: {
            type: 'linear',
            position: 'right',
            title: { display: true, text: 'Humidity (%)', color: '#39d353' },
            grid: { drawOnChartArea: false },
            ticks: { color: '#39d353' },
            min: 0,
            max: 100,
          },
        },
        plugins: {
          legend: {
            labels: { color: isDark ? '#f0f6fc' : '#1f2328' },
          },
          tooltip: {
            padding: 10,
            cornerRadius: 6,
          },
        },
      },
    });
  }

  function updateChartColors(theme) {
    if (!state.chartInstance) return;
    const isDark = theme === 'dark';
    const gridColor = isDark ? '#30363d' : '#e1e4e8';
    const textColor = isDark ? '#8b949e' : '#656d76';

    state.chartInstance.options.scales.x.grid.color = gridColor;
    state.chartInstance.options.scales.x.ticks.color = textColor;
    state.chartInstance.options.scales.yTemp.grid.color = gridColor;
    state.chartInstance.options.plugins.legend.labels.color = isDark ? '#f0f6fc' : '#1f2328';
  }

  function renderHistoryChart(records) {
    if (!state.chartInstance) return;

    // Sort ascending chronologically
    const sorted = [...records].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));

    const labels = sorted.map((r) => {
      const dt = new Date(r.timestamp);
      return dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    });

    const tempData = sorted.map((r) => Number(r.temperature));
    const humData = sorted.map((r) => Number(r.humidity));

    updateChartData(labels, tempData, humData);
  }

  function updateChartData(labels, temps, humidities) {
    if (!state.chartInstance) return;
    state.chartInstance.data.labels = labels;
    state.chartInstance.data.datasets[0].data = temps;
    state.chartInstance.data.datasets[1].data = humidities;
    state.chartInstance.update();
  }

  // Run on DOM load
  document.addEventListener('DOMContentLoaded', init);
})();
