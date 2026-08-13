const state = {
  scan: null,
  inputPath: '',
  uploadSession: '',
  selectedNames: [],
  metadata: '',
  metadataValid: false,
  classifier: '',
  qiimeEnv: '',
  environmentReady: false,
  condaAvailable: false,
  platform: '',
  busy: false,
  jobId: '',
};

const $ = (id) => document.getElementById(id);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[character]);
}

function toast(message, success = false) {
  const box = $('toast');
  if (!box) return;
  $('toastText').textContent = message;
  box.classList.toggle('success', success);
  box.classList.add('show');
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => box.classList.remove('show'), 5200);
}

async function api(url, options = {}) {
  const headers = options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' };
  const response = await fetch(url, { ...options, headers: { ...headers, ...(options.headers || {}) } });
  const raw = await response.text();
  let payload = {};
  try { payload = raw ? JSON.parse(raw) : {}; } catch { payload = { error: raw || '服务器返回了无法识别的内容' }; }
  if (!response.ok && !payload.validation) throw new Error(payload.error || `请求失败（${response.status}）`);
  return payload;
}

function setBusy(button, busy, busyText = '处理中…') {
  if (!button) return;
  if (busy) {
    if (!button.dataset.label) button.dataset.label = button.innerHTML;
    button.innerHTML = `<span class="button-spinner"></span>${busyText}`;
    button.disabled = true;
  } else {
    button.innerHTML = button.dataset.label || button.innerHTML;
    delete button.dataset.label;
  }
  updateReadiness();
}

function markStep(name, status, label) {
  const step = document.querySelector(`[data-step="${name}"]`);
  if (!step) return;
  step.classList.toggle('done', status === 'done');
  step.classList.toggle('active', status === 'active');
  step.classList.toggle('blocked', status === 'blocked');
  const stateEl = step.querySelector('.step-state');
  if (stateEl) stateEl.textContent = label || (status === 'done' ? '已完成' : status === 'blocked' ? '待处理' : '进行中');
}

function readValue(id, fallback = '') { return $(id)?.value?.trim() || fallback; }
function isChecked(id) { return Boolean($(id)?.checked); }
function selectedSamplingMode() { return document.querySelector('input[name="samplingMode"]:checked')?.value || 'auto'; }

function configPayload() {
  return {
    input_path: state.inputPath || readValue('inputPath'),
    output_dir: readValue('outputPath', 'qiime2_analysis'),
    data_type: state.scan?.data_type || '',
    metadata: state.metadata || readValue('metadataPath'),
    classifier: state.classifier || readValue('classifierPath'),
    primer_f: readValue('primerF'),
    primer_r: readValue('primerR'),
    phred_offset: Number(readValue('phredOffset', '33')),
    min_quality: Number(readValue('minQuality', '20')),
    min_frequency: Number(readValue('minFrequency', '10')),
    trim_left_f: Number(readValue('trimLeftF', '0')),
    trim_left_r: Number(readValue('trimLeftR', '0')),
    trunc_len_f: Number(readValue('truncLenF', '250')),
    trunc_len_r: Number(readValue('truncLenR', '220')),
    sampling_depth: selectedSamplingMode() === 'auto' ? 'auto' : readValue('samplingDepth'),
    qiime_env: state.qiimeEnv,
    no_trim: isChecked('noTrim'),
    no_filter: isChecked('noFilter'),
    no_figaro: isChecked('noFigaro'),
    skip_taxonomy: isChecked('skipTaxonomy'),
    skip_diversity: isChecked('skipDiversity'),
    skip_ancom: isChecked('skipAncom'),
  };
}

function renderSelectedFiles() {
  const target = $('selectedFiles');
  if (!target) return;
  if (!state.selectedNames.length) {
    target.innerHTML = '<span class="file-badge">＋</span><span><strong>还没有加入数据</strong><small>可以先选择 manifest，再选择多个 FASTQ；两次选择会自动合并。</small></span>';
    return;
  }
  const visible = state.selectedNames.slice(0, 10).map((name) => `<li>${escapeHtml(name)}</li>`).join('');
  target.innerHTML = `<span class="file-badge">✓</span><span><strong>已加入 ${state.selectedNames.length} 个文件</strong><ul>${visible}${state.selectedNames.length > 10 ? '<li>还有更多文件…</li>' : ''}</ul></span>`;
}

function dataTypeLabel(value) {
  return ({
    manifest_paired: '双端 manifest',
    manifest_single: '单端 manifest',
    Casava_paired: 'Casava 双端',
    Casava_single: 'Casava 单端',
    EMP_paired: 'EMP 双端',
    EMP_single: 'EMP 单端',
    muxed_paired: '混样双端',
    muxed_single: '混样单端',
  })[value] || value || '等待识别';
}

function showScan(scan, notify = true) {
  state.scan = scan;
  state.inputPath = scan.analysis_path || scan.path || '';
  if ($('inputPath')) $('inputPath').value = state.inputPath;
  const isManifest = Boolean(scan.data_type?.startsWith('manifest'));
  const count = Number(scan.fastq_count || 0);
  const sampleCount = Number(scan.sample_count || 0);
  const missing = Number(scan.missing_fastq_count || 0);
  const detail = isManifest
    ? `${count} 个 FASTQ 引用 · ${sampleCount || '—'} 个样本 · ${scan.paired_end ? '双端' : '单端'}`
    : `${count} 个 FASTQ 文件 · ${scan.paired_end ? '双端' : '单端'}`;
  const result = $('scanResult');
  if (result) {
    result.classList.remove('empty', 'warning');
    result.classList.toggle('warning', Boolean(scan.warnings?.length));
    result.innerHTML = `<span class="scan-icon">${scan.data_type ? '✓' : '!'}</span><span><strong>${escapeHtml(dataTypeLabel(scan.data_type))}</strong><small>${escapeHtml(scan.exists ? detail : '路径不存在，请检查输入。')}${missing ? `<br><em>${missing} 个路径暂未找到</em>` : ''}</small></span>`;
  }
  $('dataTypeValue').textContent = scan.data_type ? dataTypeLabel(scan.data_type) : '等待扫描';
  $('dataTypeDesc').textContent = scan.data_type ? (isManifest ? '已读取 manifest 表头和序列路径。' : '已根据文件名判断读段关系。') : '请重新选择文件，或检查 manifest 表头。';
  $('sampleCountValue').textContent = sampleCount || count || '—';
  $('sampleCountDesc').textContent = isManifest ? 'manifest 中的样本数；FASTQ 引用数显示在输入卡片。' : '根据 FASTQ 文件名推断的样本数。';
  $('inputStatus').textContent = scan.data_type ? `${dataTypeLabel(scan.data_type)} 已准备好` : '需要继续检查输入';
  $('inputStatus').className = `inline-status ${scan.data_type ? 'good' : 'warn'}`;
  const canGenerateManifest = scan.kind === 'directory' && count > 0 && !isManifest;
  $('manifestButton').disabled = !canGenerateManifest;
  $('metadataButton').disabled = !isManifest;
  markStep('data', scan.data_type ? 'done' : 'active', scan.data_type ? '已识别' : '进行中');
  markStep('metadata', isManifest ? 'active' : 'blocked', isManifest ? '待准备' : '先完成 manifest');
  if (notify) {
    if (scan.warnings?.length) toast(scan.warnings[0]);
    else if (scan.data_type) toast('数据已识别，可以继续准备 metadata。', true);
  }
  updateReadiness();
}

async function scan() {
  const path = readValue('inputPath');
  if (!path) { toast('请先选择文件，或输入 Linux 服务器上的数据路径。'); return; }
  setBusy($('scanButton'), true, '正在检查…');
  try { showScan((await api(`/api/scan?path=${encodeURIComponent(path)}`)).scan); }
  catch (error) { toast(error.message); }
  finally { setBusy($('scanButton'), false); }
}

async function uploadFiles(input, kind) {
  if (!input.files?.length) return;
  const files = [...input.files];
  files.forEach((file) => { if (!state.selectedNames.includes(file.name)) state.selectedNames.push(file.name); });
  renderSelectedFiles();
  const body = new FormData();
  body.append('kind', kind);
  if (state.uploadSession) body.append('session_id', state.uploadSession);
  files.forEach((file) => body.append('files', file, file.name));
  input.value = '';
  try {
    const result = await api('/api/upload', { method: 'POST', body });
    state.uploadSession = result.session_id || state.uploadSession;
    if (kind === 'classifier') {
      state.classifier = result.path;
      $('classifierPath').value = result.path;
      $('classifierName').textContent = '已选择并暂存到服务器';
      toast('分类器已准备好。', true);
      updateReadiness();
      return;
    }
    if (kind === 'metadata') {
      state.metadata = result.path;
      $('metadataPath').value = result.path;
      $('metadataName').textContent = '已选择并暂存到服务器';
      await validateMetadata(result.path);
      return;
    }
    showScan(result.scan);
    toast(kind === 'fastq' ? 'FASTQ 已加入同一个数据包。' : 'manifest 已加入，程序会自动匹配同目录序列。', true);
  } catch (error) {
    toast(`文件处理失败：${error.message}`);
  }
}

async function generateManifest() {
  if (!state.scan || state.scan.kind !== 'directory') return;
  setBusy($('manifestButton'), true, '正在生成…');
  try {
    const data = await api('/api/manifest', { method: 'POST', body: JSON.stringify({ input_path: state.inputPath, paired_end: state.scan.paired_end }) });
    showScan(data.scan);
    toast('manifest 已生成，后续可以直接准备 metadata。', true);
  } catch (error) { toast(error.message); }
  finally { setBusy($('manifestButton'), false); }
}

async function generateMetadata() {
  if (!state.inputPath || !state.scan?.data_type?.startsWith('manifest')) { toast('请先选择或生成 manifest。'); return; }
  setBusy($('metadataButton'), true, '正在生成…');
  try {
    const data = await api('/api/metadata', { method: 'POST', body: JSON.stringify({ source_path: state.inputPath, columns: ['group'] }) });
    state.metadata = data.path;
    state.metadataValid = false;
    $('metadataPath').value = data.path;
    $('metadataName').textContent = '已生成模板，请确认分组值';
    await validateMetadata(data.path);
    toast(`metadata 模板已生成，包含 ${data.sample_count} 个样本。`, true);
  } catch (error) { toast(error.message); }
  finally { setBusy($('metadataButton'), false); }
}

async function validateMetadata(path, silent = false) {
  if (!path) { state.metadataValid = false; updateReadiness(); return false; }
  try {
    const data = await api('/api/validate-metadata', { method: 'POST', body: JSON.stringify({ path }) });
    state.metadataValid = Boolean(data.validation?.valid);
    const validation = data.validation || {};
    $('metadataValidation').textContent = state.metadataValid
      ? `✓ 已通过 · ${validation.sample_count || 0} 个样本 · ${validation.columns?.length || 0} 列`
      : `! ${[...(validation.errors || []), ...(validation.warnings || [])].join('；') || 'metadata 需要修正'}`;
    $('metadataValidation').className = `validation-note ${state.metadataValid ? 'good' : 'warn'}`;
    if (!state.metadataValid && !silent) toast((validation.errors || ['metadata 校验失败']).join('；'));
    if (state.metadataValid) markStep('metadata', 'done', '已通过');
  } catch (error) {
    state.metadataValid = false;
    $('metadataValidation').textContent = error.message;
    $('metadataValidation').className = 'validation-note warn';
    if (!silent) toast(error.message);
  }
  updateReadiness();
  return state.metadataValid;
}

function environmentLabel(environment) {
  if (environment.qiime_available) return `${environment.name} · QIIME2 ${environment.qiime_version || '可用'}${environment.active ? ' · 当前环境' : ''}`;
  return `${environment.name} · 未找到 QIIME2${environment.active ? ' · 当前环境' : ''}`;
}

async function loadEnvironments() {
  const select = $('environmentSelect');
  select.innerHTML = '<option value="">正在执行 conda env list…</option>';
  $('refreshEnvironments').disabled = true;
  try {
    const data = await api('/api/environments');
    state.condaAvailable = Boolean(data.conda_available);
    select.innerHTML = '<option value="">请选择可用的 QIIME2 环境</option>';
    const usable = [];
    (data.environments || []).forEach((environment) => {
      const option = document.createElement('option');
      option.value = environment.name;
      option.textContent = environmentLabel(environment);
      option.disabled = !environment.qiime_available;
      select.appendChild(option);
      if (environment.qiime_available) usable.push(environment);
    });
    state.environmentReady = usable.length > 0;
    if (usable.length) {
      const active = usable.find((environment) => environment.active) || usable[0];
      state.qiimeEnv = active.name;
      select.value = active.name;
      $('environmentStatus').textContent = `✓ 已选中 ${environmentLabel(active)}，点击底部按钮即可运行。`;
      $('environmentStatus').className = 'environment-status good';
      $('qiimeValue').textContent = 'READY';
      $('qiimeDesc').textContent = `已找到 ${usable.length} 个可用的 QIIME2 环境。`;
      $('installHelper').hidden = true;
      markStep('runtime', 'done', '已就绪');
    } else {
      state.qiimeEnv = '';
      $('environmentStatus').textContent = data.error || (state.condaAvailable ? '找到了 Conda，但没有环境包含 QIIME2。' : '没有找到 Conda，请在 Linux 服务器检查安装。');
      $('environmentStatus').className = 'environment-status warn';
      $('qiimeValue').textContent = 'NEEDS INSTALL';
      $('qiimeDesc').textContent = state.condaAvailable ? 'Conda 存在，但当前环境都没有 QIIME2。' : '当前服务没有找到 Conda。';
      $('installHelper').hidden = false;
      markStep('runtime', 'blocked', '需要安装');
      await loadInstallOptions();
    }
  } catch (error) {
    state.environmentReady = false;
    $('environmentStatus').textContent = error.message;
    $('environmentStatus').className = 'environment-status warn';
    $('qiimeValue').textContent = 'CHECK FAILED';
    $('qiimeDesc').textContent = '环境检查失败，请点击重新检查。';
    $('installHelper').hidden = false;
    markStep('runtime', 'blocked', '检查失败');
    await loadInstallOptions();
  } finally {
    $('refreshEnvironments').disabled = false;
    updateReadiness();
  }
}

async function checkHealth() {
  try {
    const data = await api('/api/health');
    state.platform = data.platform || '';
    state.condaAvailable = Boolean(data.tools?.conda);
    $('healthDot').classList.add('good');
    $('healthText').textContent = state.platform === 'posix' ? 'Linux · 本地服务正常' : '当前是开发机 · 目标运行环境为 Linux';
    $('qiimeValue').textContent = data.tools?.qiime ? 'DIRECT' : '检测中';
    $('qiimeDesc').textContent = data.tools?.qiime ? '当前进程可以直接调用 QIIME2。' : '正在逐个检查 Conda 环境中的 QIIME2。';
  } catch (error) {
    $('healthText').textContent = '本地服务连接失败';
    $('qiimeValue').textContent = 'OFFLINE';
    toast(error.message);
  }
  await loadEnvironments();
}

function updateInstallSummary() {
  const version = readValue('installVersion', '2024.10');
  const distribution = readValue('installDistribution', 'amplicon');
  const name = readValue('installEnvironmentName') || `qiime2-${distribution}-${version}`;
  $('installSummary').textContent = `一键创建 Conda 环境：${name} · ${version} · ${distribution === 'amplicon' ? '扩增子分析版' : '轻量版'}`;
  const allowed = state.platform === 'posix' && state.condaAvailable;
  $('installButton').disabled = !allowed;
  $('installHint').textContent = state.platform !== 'posix' ? '当前是 Windows 开发机；请在 Linux + Conda 服务器上使用一键安装。' : allowed ? '安装过程可能需要几分钟，页面会持续显示进度。' : '没有找到 Conda，无法开始安装。';
}

async function loadInstallOptions() {
  try {
    const data = await api('/api/install-options');
    $('installVersion').innerHTML = (data.versions || []).map((value) => `<option value="${escapeHtml(value)}">QIIME2 ${escapeHtml(value)}</option>`).join('');
    $('installDistribution').innerHTML = (data.distributions || []).map((value) => `<option value="${escapeHtml(value)}">${value === 'amplicon' ? '扩增子分析版' : '轻量版'}</option>`).join('');
    updateInstallSummary();
  } catch (error) { $('installSummary').textContent = error.message; }
}

async function installQiime() {
  if (state.platform !== 'posix') { toast('一键安装只支持 Linux，请在目标服务器打开页面。'); return; }
  const payload = { version: readValue('installVersion'), distribution: readValue('installDistribution'), environment_name: readValue('installEnvironmentName') || undefined };
  setBusy($('installButton'), true, '正在创建环境…');
  $('installStatus').textContent = '正在连接 Conda，安装期间不要关闭页面。';
  try {
    const data = await api('/api/install', { method: 'POST', body: JSON.stringify(payload) });
    const poll = async () => {
      try {
        const current = await api(`/api/install-jobs/${data.job_id}`);
        $('installStatus').textContent = current.job.output || current.job.status;
        if (current.job.status === 'running') { window.setTimeout(poll, 1500); return; }
        setBusy($('installButton'), false);
        await loadEnvironments();
        toast(current.job.status === 'completed' ? 'QIIME2 环境安装完成，请选择它后开始分析。' : '安装没有完成，请查看页面中的失败信息。', current.job.status === 'completed');
      } catch (error) {
        setBusy($('installButton'), false);
        $('installStatus').textContent = error.message;
        toast(error.message);
      }
    };
    window.setTimeout(poll, 700);
  } catch (error) {
    setBusy($('installButton'), false);
    toast(error.message);
  }
}

function samplingIsValid() {
  if (selectedSamplingMode() === 'auto') return true;
  const value = Number(readValue('samplingDepth'));
  return Number.isInteger(value) && value > 0;
}

function updateSamplingMode() {
  const custom = selectedSamplingMode() === 'custom';
  $('samplingDepth').disabled = !custom;
  $('samplingDepth').setAttribute('aria-disabled', String(!custom));
  $('samplingDepthHelp').textContent = custom
    ? '自定义值会直接用于多样性分析；建议不要高于大多数样本的测序深度。'
    : '自动模式会先查看每个样本的有效序列量，再选择尽量保留样本的共同深度；第一次分析推荐使用它。';
  $('samplingDepthHelp').className = `field-help ${samplingIsValid() ? '' : 'warn'}`;
  updateReadiness();
}

function updateReadiness() {
  const dataReady = Boolean(state.scan?.data_type && state.inputPath);
  const metadataReady = Boolean(state.metadata && state.metadataValid);
  const classifierReady = isChecked('skipTaxonomy') || Boolean(state.classifier || readValue('classifierPath'));
  const depthReady = samplingIsValid();
  const runtimeReady = Boolean(state.environmentReady && state.qiimeEnv);
  const checks = {
    data: [dataReady, dataReady ? `${dataTypeLabel(state.scan.data_type)} · ${state.scan.fastq_count || 0} 个 FASTQ 引用` : '先选择并扫描 manifest / FASTQ'],
    metadata: [metadataReady, metadataReady ? 'metadata 已校验，可以开始分析' : '请选择或生成 metadata，并完成校验'],
    runtime: [runtimeReady, runtimeReady ? `使用 Conda 环境 ${state.qiimeEnv}` : '等待可用的 QIIME2 Conda 环境'],
    classifier: [classifierReady, classifierReady ? (isChecked('skipTaxonomy') ? '已跳过物种分类' : '分类器已准备好') : '请选择分类器，或勾选跳过物种分类'],
    sampling: [depthReady, depthReady ? (selectedSamplingMode() === 'auto' ? '自动计算，适合首次分析' : `固定深度 ${readValue('samplingDepth')}`) : '自定义采样深度必须是正整数'],
  };
  Object.entries(checks).forEach(([key, [ok, text]]) => {
    const item = document.querySelector(`[data-check="${key}"]`);
    if (!item) return;
    item.classList.toggle('good', ok);
    item.classList.toggle('warn', !ok);
    const dot = item.querySelector('.check-dot');
    const copy = item.querySelector('.check-copy');
    if (dot) dot.textContent = ok ? '✓' : '!';
    if (copy) copy.textContent = text;
  });
  const ready = Object.values(checks).every(([ok]) => ok);
  state.canRun = ready;
  $('runButton').disabled = !ready || state.busy;
  $('runHint').textContent = ready ? '所有必要信息已准备好，点击一次即可启动完整分析。' : '还差一步：按照上面的提示补齐必要信息。';
  $('readyBadge').textContent = ready ? 'READY' : 'NEEDS SETUP';
  $('readyBadge').className = `ready-badge ${ready ? 'good' : 'warn'}`;
}

function renderJob(job) {
  const steps = job.result?.steps || [];
  const stepBox = $('runSteps');
  if (stepBox && steps.length) {
    stepBox.innerHTML = steps.map((step) => `<div class="run-step ${step.status === 'failed' ? 'failed' : 'done'}"><span>${step.status === 'failed' ? '!' : '✓'}</span><strong>${escapeHtml(step.name)}</strong><small>${escapeHtml(step.output || step.status)}</small></div>`).join('');
  }
  $('runStatus').textContent = job.message || (job.status === 'running' ? '正在运行，请耐心等待…' : job.status);
  $('runStatus').className = `run-status ${job.status === 'completed' ? 'good' : job.status === 'failed' ? 'bad' : 'running'}`;
  if (job.status === 'completed' && job.result) {
    $('resultCard').hidden = false;
    $('resultOutput').textContent = job.result.output_dir || '已完成';
    $('resultReport').textContent = job.result.report || '报告已生成';
  }
}

async function runAnalysis() {
  state.inputPath = readValue('inputPath');
  state.metadata = readValue('metadataPath');
  state.classifier = state.classifier || readValue('classifierPath');
  if (!state.scan?.data_type || !state.inputPath) { toast('请先选择并扫描数据。'); return; }
  if (state.metadata && !state.metadataValid && !(await validateMetadata(state.metadata))) return;
  updateReadiness();
  if (!state.canRun) { toast('请按照页面上的提示补齐设置。'); return; }
  const data = configPayload();
  state.busy = true;
  setBusy($('runButton'), true, '分析启动中…');
  $('resultCard').hidden = true;
  $('runSteps').innerHTML = '<div class="run-step running"><span class="button-spinner"></span><strong>正在启动 QIIME2</strong><small>正在创建任务</small></div>';
  $('runStatus').textContent = '任务已提交，页面会自动更新进度。';
  $('runStatus').className = 'run-status running';
  markStep('run', 'active', '运行中');
  try {
    const started = await api('/api/run', { method: 'POST', body: JSON.stringify(data) });
    state.jobId = started.job_id;
    const poll = async () => {
      try {
        const current = await api(`/api/jobs/${state.jobId}`);
        renderJob(current.job);
        if (current.job.status === 'running') { window.setTimeout(poll, 1200); return; }
        state.busy = false;
        setBusy($('runButton'), false);
        markStep('run', current.job.status === 'completed' ? 'done' : 'blocked', current.job.status === 'completed' ? '已完成' : '失败');
        toast(current.job.status === 'completed' ? '分析完成，结果已经写入输出目录。' : `分析未完成：${current.job.message || '请查看结果区。'}`, current.job.status === 'completed');
      } catch (error) {
        state.busy = false;
        setBusy($('runButton'), false);
        $('runStatus').textContent = error.message;
        $('runStatus').className = 'run-status bad';
        toast(error.message);
      }
    };
    window.setTimeout(poll, 700);
  } catch (error) {
    state.busy = false;
    setBusy($('runButton'), false);
    $('runStatus').textContent = error.message;
    $('runStatus').className = 'run-status bad';
    toast(error.message);
  }
}

function bind(id, event, handler) { $(id)?.addEventListener(event, handler); }

document.addEventListener('DOMContentLoaded', () => {
  renderSelectedFiles();
  updateSamplingMode();
  bind('scanButton', 'click', scan);
  bind('inputPath', 'keydown', (event) => { if (event.key === 'Enter') scan(); });
  bind('inputPath', 'input', () => { state.inputPath = readValue('inputPath'); state.scan = null; updateReadiness(); });
  bind('fastqPicker', 'change', (event) => uploadFiles(event.target, 'fastq'));
  bind('manifestPicker', 'change', (event) => uploadFiles(event.target, 'manifest'));
  bind('classifierPicker', 'change', (event) => uploadFiles(event.target, 'classifier'));
  bind('metadataPicker', 'change', (event) => uploadFiles(event.target, 'metadata'));
  bind('manifestButton', 'click', generateManifest);
  bind('metadataButton', 'click', generateMetadata);
  bind('runButton', 'click', runAnalysis);
  bind('refreshEnvironments', 'click', loadEnvironments);
  bind('environmentSelect', 'change', (event) => {
    state.qiimeEnv = event.target.value;
    state.environmentReady = Boolean(state.qiimeEnv);
    $('environmentStatus').textContent = state.qiimeEnv ? `✓ 已选择 ${state.qiimeEnv}` : '请选择包含 QIIME2 的环境';
    $('environmentStatus').className = `environment-status ${state.qiimeEnv ? 'good' : 'warn'}`;
    updateReadiness();
  });
  bind('installVersion', 'change', updateInstallSummary);
  bind('installDistribution', 'change', updateInstallSummary);
  bind('installEnvironmentName', 'input', updateInstallSummary);
  bind('installButton', 'click', installQiime);
  bind('metadataPath', 'input', () => { state.metadata = readValue('metadataPath'); state.metadataValid = false; $('metadataName').textContent = state.metadata ? '等待校验服务器路径' : '未选择 metadata'; updateReadiness(); });
  bind('metadataPath', 'blur', () => validateMetadata(readValue('metadataPath'), true));
  bind('classifierPath', 'input', () => { state.classifier = readValue('classifierPath'); $('classifierName').textContent = state.classifier ? '使用服务器路径' : '未选择分类器'; updateReadiness(); });
  $$('input[name="samplingMode"]').forEach((input) => input.addEventListener('change', updateSamplingMode));
  ['samplingDepth', 'skipTaxonomy', 'noTrim', 'noFilter', 'noFigaro', 'skipDiversity', 'skipAncom'].forEach((id) => bind(id, 'input', updateReadiness));
  checkHealth();
});
