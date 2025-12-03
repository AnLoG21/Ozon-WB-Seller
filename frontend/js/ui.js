/**
 * Управление UI элементами
 */
class UIManager {
  constructor() {
    this.elements = {
      mpOzonBtn: document.getElementById('mp-ozon'),
      mpWbBtn: document.getElementById('mp-wb'),
      envSelect: document.getElementById('env-select'),
      connStatus: document.getElementById('conn-status'),
      logEl: document.getElementById('log'),
      priceMetricsEl: document.getElementById('price-metrics'),
      stockMetricsEl: document.getElementById('stock-metrics'),
      btnBuildPayload: document.getElementById('btn-build-payload'),
      btnSend: document.getElementById('btn-send'),
      btnClearLog: document.getElementById('btn-clear-log'),
    };
  }

  updateMarketplaceUI(marketplace) {
    if (marketplace === 'ozon') {
      this.elements.mpOzonBtn.classList.add('active');
      this.elements.mpWbBtn.classList.remove('active');
    } else {
      this.elements.mpOzonBtn.classList.remove('active');
      this.elements.mpWbBtn.classList.add('active');
    }
  }

  addLog(level, message, payload) {
    const div = document.createElement('div');
    div.className = 'log-entry';
    // ... (создание структуры логовой записи)
    this.elements.logEl.appendChild(div);
    this.elements.logEl.scrollTop = this.elements.logEl.scrollHeight;
  }

  clearLog() {
    this.elements.logEl.innerHTML = '';
  }
}

const uiManager = new UIManager();
