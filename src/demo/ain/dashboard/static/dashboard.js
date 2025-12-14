// AI Dashboard WebSocket Client

class DashboardClient {
    constructor() {
        this.ws = null;
        this.reconnectInterval = 3000;
        this.maxReconnectAttempts = 10;
        this.reconnectAttempts = 0;

        // State
        this.intents = new Map();
        this.deviations = [];
        this.commands = [];
        this.maxHistoryItems = 50;

        // UI Elements
        this.statusDot = document.getElementById('statusDot');
        this.statusText = document.getElementById('statusText');
        this.intentsList = document.getElementById('intentsList');
        this.deviationsList = document.getElementById('deviationsList');
        this.commandsList = document.getElementById('commandsList');
        this.intentCount = document.getElementById('intentCount');
        this.deviationCount = document.getElementById('deviationCount');
        this.commandCount = document.getElementById('commandCount');

        // Metrics
        this.gnbLatency = document.getElementById('gnbLatency');
        this.cellId = document.getElementById('cellId');
        this.ueCountBadge = document.getElementById('ueCountBadge');
        this.ueMetricsList = document.getElementById('ueMetricsList');

        this.connect();
    }

    connect() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.hostname}:8081`;

        try {
            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => this.onConnect();
            this.ws.onmessage = (event) => this.onMessage(event);
            this.ws.onclose = () => this.onDisconnect();
            this.ws.onerror = (error) => this.onError(error);
        } catch (error) {
            console.error('WebSocket connection error:', error);
            this.scheduleReconnect();
        }
    }

    onConnect() {
        console.log('Connected to AI Dashboard');
        this.reconnectAttempts = 0;
        this.updateConnectionStatus(true);
    }

    onDisconnect() {
        console.log('Disconnected from AI Dashboard');
        this.updateConnectionStatus(false);
        this.scheduleReconnect();
    }

    onError(error) {
        console.error('WebSocket error:', error);
    }

    onMessage(event) {
        try {
            const message = JSON.parse(event.data);

            switch (message.type) {
                case 'connected':
                    console.log('Dashboard connected:', message.message);
                    break;
                case 'intent':
                    this.handleIntent(message.data);
                    break;
                case 'deviation':
                    this.handleDeviation(message.data);
                    break;
                case 'command':
                    this.handleCommand(message.data);
                    break;
                case 'kpi':
                    this.handleKPI(message.data);
                    break;
                default:
                    console.log('Unknown message type:', message.type);
            }
        } catch (error) {
            console.error('Error parsing message:', error);
        }
    }

    scheduleReconnect() {
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            console.log(`Reconnecting in ${this.reconnectInterval}ms (attempt ${this.reconnectAttempts})`);
            setTimeout(() => this.connect(), this.reconnectInterval);
        }
    }

    updateConnectionStatus(connected) {
        if (connected) {
            this.statusDot.classList.add('connected');
            this.statusText.textContent = 'Connected';
        } else {
            this.statusDot.classList.remove('connected');
            this.statusText.textContent = 'Disconnected';
        }
    }

    handleIntent(data) {
        if (!data || Object.keys(data).length === 0) {
            // Empty intent = clear all
            this.intents.clear();
        } else {
            const intentId = data.intent_id || 'unknown';
            this.intents.set(intentId, data);
        }
        this.renderIntents();
    }

    handleDeviation(data) {
        this.deviations.unshift({
            ...data,
            timestamp: new Date().toISOString()
        });

        // Keep only recent items
        if (this.deviations.length > this.maxHistoryItems) {
            this.deviations = this.deviations.slice(0, this.maxHistoryItems);
        }

        this.renderDeviations();
    }

    handleCommand(data) {
        this.commands.unshift({
            ...data,
            timestamp: new Date().toISOString()
        });

        // Keep only recent items
        if (this.commands.length > this.maxHistoryItems) {
            this.commands = this.commands.slice(0, this.maxHistoryItems);
        }

        this.renderCommands();
    }

    handleKPI(data) {
        const { cell, ues } = data;

        // Update gNB latency
        if (cell && cell.DRB_PdcpSduDelayDl !== undefined) {
            this.gnbLatency.textContent = cell.DRB_PdcpSduDelayDl.toFixed(2);
        }

        // Update cell ID
        if (cell && cell.cell_id) {
            this.cellId.textContent = cell.cell_id;
        }

        // Update UE metrics
        if (ues && ues.length > 0) {
            this.ueCountBadge.textContent = ues.length;
            this.renderUEMetrics(ues);
        } else {
            this.ueCountBadge.textContent = '0';
            this.ueMetricsList.innerHTML = '<div class="empty-state">No UE data</div>';
        }
    }

    renderUEMetrics(ues) {
        const html = ues.map(ue => {
            const ueId = ue.ue_id || 'Unknown';
            const latency = ue.UE_DRB_PdcpSduDelayDl_UEID;
            const throughput = ue.UE_DRB_UEThpDl_UEID;
            const bler = ue.UE_DRB_BlerDl_UEID;

            return `
                <div class="ue-card">
                    <div class="ue-id">📱 UE: ${this.escapeHtml(ueId)}</div>
                    ${latency !== undefined ? `
                        <div class="ue-metric-row">
                            <span class="label">Latency:</span>
                            <span class="value">${latency.toFixed(2)} ms</span>
                        </div>
                    ` : ''}
                    ${throughput !== undefined ? `
                        <div class="ue-metric-row">
                            <span class="label">Throughput:</span>
                            <span class="value">${(throughput / 1e6).toFixed(2)} Mbps</span>
                        </div>
                    ` : ''}
                    ${bler !== undefined ? `
                        <div class="ue-metric-row">
                            <span class="label">BLER:</span>
                            <span class="value">${(bler * 100).toFixed(2)}%</span>
                        </div>
                    ` : ''}
                </div>
            `;
        }).join('');

        this.ueMetricsList.innerHTML = html;
    }

    renderIntents() {
        this.intentCount.textContent = this.intents.size;

        if (this.intents.size === 0) {
            this.intentsList.innerHTML = '<div class="empty-state">No active intents</div>';
            return;
        }

        const html = Array.from(this.intents.values()).map(intent => `
            <div class="intent-card">
                <div class="intent-type">${this.escapeHtml(intent.type || 'UNKNOWN')}</div>
                <div class="intent-metric">Metric: ${this.escapeHtml(intent.metric || 'N/A')}</div>
                <div class="intent-metric">Target: ${intent.target !== undefined ? intent.target.toFixed(2) : 'N/A'} (${this.escapeHtml(intent.direction || 'N/A')})</div>
                <div class="intent-scope">Scope: ${this.formatScope(intent.scope)}</div>
            </div>
        `).join('');

        this.intentsList.innerHTML = html;
    }

    renderDeviations() {
        this.deviationCount.textContent = this.deviations.length;

        if (this.deviations.length === 0) {
            this.deviationsList.innerHTML = '<div class="empty-state">No deviations detected</div>';
            return;
        }

        const html = this.deviations.slice(0, 20).map(dev => `
            <div class="deviation-item">
                <div class="deviation-metric">${this.escapeHtml(dev.metric || 'Unknown')}</div>
                <div class="deviation-value">Value: ${dev.value !== undefined ? dev.value.toFixed(2) : 'N/A'} | Severity: ${this.escapeHtml(dev.severity || 'N/A')}</div>
                <div class="deviation-value">Scope: ${this.formatScope(dev.scope)}</div>
                <div class="deviation-time">${this.formatTime(dev.timestamp)}</div>
            </div>
        `).join('');

        this.deviationsList.innerHTML = html;
    }

    renderCommands() {
        this.commandCount.textContent = this.commands.length;

        if (this.commands.length === 0) {
            this.commandsList.innerHTML = '<div class="empty-state">No commands sent</div>';
            return;
        }

        const html = this.commands.slice(0, 20).map(cmd => `
            <div class="command-item">
                <div class="command-name">${this.escapeHtml(cmd.command || 'Unknown')}</div>
                <div class="command-params">${this.formatParams(cmd.params)}</div>
                <div class="command-time">${this.formatTime(cmd.timestamp)}</div>
            </div>
        `).join('');

        this.commandsList.innerHTML = html;
    }

    formatScope(scope) {
        if (!scope) return 'N/A';
        const parts = [];
        if (scope.ue_id) parts.push(`UE: ${scope.ue_id}`);
        if (scope.cell_id) parts.push(`Cell: ${scope.cell_id}`);
        return parts.length > 0 ? parts.join(', ') : 'Global';
    }

    formatParams(params) {
        if (!params) return 'N/A';
        return JSON.stringify(params);
    }

    formatTime(timestamp) {
        if (!timestamp) return '';
        const date = new Date(timestamp);
        return date.toLocaleTimeString();
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialize dashboard when page loads
document.addEventListener('DOMContentLoaded', () => {
    new DashboardClient();
});
