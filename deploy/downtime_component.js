export default {
  data() {
    return {
      savedProductionLine: 'Unknown Line', cachedQuality: [], cachedSchedule: [],
      cachedProduction: [], cachedPlannedStop: [],
      formData: {downtimeCategory: null, presetTime: 0, durationMinutes: null, machineIssue: '', actionTaken: '', remarks: ''},
      selectedReasonObj: null, cleanReasonName: '', timerMode: 'stopwatch', timeLeftMs: 0,
      elapsedMs: 0, timerStartTime: null, timerTargetTime: null, timerRunning: false,
      timerInterval: null, updateTimeout: null, requestTimeout: null, applyingShared: false,
      sessionId: null, sessionStatus: 'idle', revision: 0, sharedElapsedMs: 0,
      serverOffset: 0, lastServerSeen: 0, ready: false, busy: false, commandId: null, syncError: '',
    };
  },
  mounted() {
    // Browser timers render only. Python owns countdown completion and logging.
    localStorage.removeItem('downtime_active_timer');
    this.timerInterval = setInterval(() => this.renderSharedTime(), 200);
    this.sendCommand('get');
  },
  beforeUnmount() {
    clearInterval(this.timerInterval);
    clearTimeout(this.updateTimeout);
    clearTimeout(this.requestTimeout);
  },
  watch: {
    msg: {handler(msg) {
      if (msg?.payload?.productionLine) this.savedProductionLine = msg.payload.productionLine;
      for (const [field, cache] of [['qualityDowntime', 'cachedQuality'], ['scheduleDowntime', 'cachedSchedule'],
                                   ['productionDowntime', 'cachedProduction'], ['plannedStopDowntime', 'cachedPlannedStop']]) {
        if (msg?.[field]) this[cache] = msg[field];
      }
      if (msg?.topic === 'downtime_state') this.applyShared(msg.payload);
      // Shift-end cleanup is handled once on the server; no browser submits a duplicate log.
    }, deep: true, immediate: true},
    formData: {handler() {
      if (this.applyingShared || !this.ready || this.busy) return;
      clearTimeout(this.updateTimeout);
      this.updateTimeout = setTimeout(() => this.sendCommand('update', this.isSessionLocked ? {actionTaken: this.formData.actionTaken, remarks: this.formData.remarks} : this.collectData()), 400);
    }, deep: true},
    selectedReasonObj(reason) {
      if (!reason || this.applyingShared || this.isSessionLocked) return;
      this.cleanReasonName = reason.title;
      this.formData.presetTime = Math.max(0, Number(reason.time) || 0);
      this.timerMode = this.formData.presetTime > 0 ? 'countdown' : 'stopwatch';
    },
  },
  computed: {
    isSessionLocked() {return this.sessionStatus === 'running' || this.sessionStatus === 'stopped';},
    controlsDisabled() {return !this.ready || this.busy || Date.now() - this.lastServerSeen > 8000;},
    dynamicReasons() {
      const lists = {Quality: this.cachedQuality, Schedule: this.cachedSchedule, Production: this.cachedProduction};
      const list = lists[this.formData.downtimeCategory] || [];
      return list.map(item => Array.isArray(item) ? {title: String(item[0]), time: Number(item[1]) || 0} :
        typeof item === 'object' ? {title: item.Name || item.title || item.name || 'Unknown',
          time: Number(item.Downtime ?? item.time ?? item.value) || 0} : {title: String(item), time: 0});
    },
    formattedTime() {
      const ms = Math.max(0, this.timerMode === 'countdown' ? this.timeLeftMs : this.elapsedMs);
      return `${String(Math.floor(ms / 60000)).padStart(2, '0')}:${String(Math.floor(ms % 60000 / 1000)).padStart(2, '0')}`;
    },
  },
  methods: {
    collectData() {return {...this.formData, cleanReasonName: this.cleanReasonName};},
    sendCommand(action, data = {}) {
      clearTimeout(this.updateTimeout);
      if (action !== 'get' && this.controlsDisabled) return;
      this.commandId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      this.busy = true;
      this.send({topic: 'downtime_command', payload: {action, data, command_id: this.commandId,
        session_id: this.sessionId, revision: this.revision}});
      clearTimeout(this.requestTimeout);
      this.requestTimeout = setTimeout(() => {
        this.busy = false;
        this.syncError = 'No server response. Check the connection; the shared timer may still be running.';
      }, 8000);
    },
    applyShared(reply) {
      const state = reply?.state;
      if (!state || state.revision < this.revision) return;
      this.lastServerSeen = Date.now();
      this.ready = true;
      this.serverOffset = Number(reply.server_ms) - Date.now();
      if (reply.command_id === this.commandId) {
        this.busy = false;
        clearTimeout(this.requestTimeout);
        this.syncError = reply.error || '';
      }
      if (state.revision === this.revision && this.sessionStatus === state.status && this.sessionId === state.id) {
        this.renderSharedTime();
        return;
      }
      this.applyingShared = true;
      this.revision = state.revision;
      this.sessionId = state.id;
      this.sessionStatus = state.status;
      this.timerRunning = state.status === 'running';
      this.timerStartTime = state.started_ms;
      this.timerTargetTime = state.target_ms;
      this.sharedElapsedMs = state.elapsed_ms || 0;
      this.formData = {downtimeCategory: null, presetTime: 0, durationMinutes: null,
        machineIssue: '', actionTaken: '', remarks: '', ...state.data};
      this.cleanReasonName = state.data.cleanReasonName || '';
      this.selectedReasonObj = this.cleanReasonName ? {title: this.cleanReasonName, time: Number(state.data.presetTime) || 0} : null;
      this.timerMode = state.target_ms ? 'countdown' : (Number(state.data.presetTime) > 0 ? 'countdown' : 'stopwatch');
      if (state.status === 'stopped') this.formData.durationMinutes = Math.max(1, Math.ceil(this.sharedElapsedMs / 60000));
      this.renderSharedTime();
      this.$nextTick(() => {this.applyingShared = false;});
    },
    renderSharedTime() {
      this.elapsedMs = this.timerRunning ? Math.max(0, Date.now() + this.serverOffset - this.timerStartTime) : this.sharedElapsedMs;
      this.timeLeftMs = this.timerTargetTime ? Math.max(0, this.timerTargetTime -
        (this.timerRunning ? Date.now() + this.serverOffset : this.timerStartTime + this.sharedElapsedMs)) : Number(this.formData.presetTime) * 60000;
    },
    setCategory(category) {
      if (this.isSessionLocked || this.controlsDisabled) return;
      this.formData = {downtimeCategory: category, presetTime: 0, durationMinutes: null,
        machineIssue: '', actionTaken: '', remarks: ''};
      this.selectedReasonObj = null;
      this.cleanReasonName = '';
      this.timerMode = 'stopwatch';
      this.sharedElapsedMs = 0;
    },
    toggleTimer() {
      this.sendCommand(this.timerRunning ? 'stop' : 'start', this.collectData());
    },
    cancelDowntime() {
      this.sendCommand('cancel');
    },
    submitDowntime() {
      this.sendCommand('log', this.collectData());
    },
  },
};
