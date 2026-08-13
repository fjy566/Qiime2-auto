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
  environments: [],
  selectedEnvironment: null,
  metadataEditor: null,
  manifestEditor: null,
  classifierCatalog: [],
  directory: null,
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
  if (isManifest) window.setTimeout(() => loadManifestPreview(state.inputPath), 0);
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
  if (kind === 'fastq' || kind === 'manifest') files.forEach((file) => { if (!state.selectedNames.includes(file.name)) state.selectedNames.push(file.name); });
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
      await loadMetadataPreview(result.path);
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
    await loadManifestPreview(data.path);
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
    await loadMetadataPreview(data.path);
    await validateMetadata(data.path);
    toast(`metadata 模板已生成，包含 ${data.sample_count} 个样本。`, true);
  } catch (error) { toast(error.message); }
  finally { setBusy($('metadataButton'), false); }
}

function metadataGroupColumn(editor = state.metadataEditor) {
  if (!editor) return '';
  return editor.headers.find((header) => header.toLowerCase() === 'group') || editor.headers.find((header) => header.toLowerCase().includes('group')) || editor.headers[1] || '';
}

function renderMetadataGroups() {
  const target = $('groupList');
  const editor = state.metadataEditor;
  if (!target || !editor) return;
  const column = metadataGroupColumn(editor);
  const existing = editor.rows.map((row) => row[column] || '').filter(Boolean);
  editor.groups = [...new Set([...(editor.groups || []), ...existing])];
  target.innerHTML = editor.groups.length
    ? editor.groups.map((group) => `<button type="button" class="group-chip" data-remove-group="${escapeHtml(group)}"><span>${escapeHtml(group)}</span><b>×</b></button>`).join('')
    : '<span class="group-empty">还没有组；先新建 control、treatment 等组</span>';
}

function renderMetadataTable() {
  const table = $('metadataEditorTable');
  const editor = state.metadataEditor;
  if (!table || !editor) return;
  const groupColumn = metadataGroupColumn(editor);
  const groupOptions = [...new Set([...(editor.groups || []), ...editor.rows.map((row) => row[groupColumn] || '').filter(Boolean)])];
  const header = editor.headers.map((column, index) => `<th><div>${escapeHtml(column)}${index ? `<small>${escapeHtml(editor.types[index] || '未声明')}</small>` : '<small>样本 ID</small>'}</div>${index ? `<button type="button" class="table-delete" data-remove-column="${escapeHtml(column)}" title="删除这一列">×</button>` : ''}</th>`).join('');
  const body = editor.rows.map((row, rowIndex) => {
    const cells = editor.headers.map((column, columnIndex) => {
      const value = row[column] || '';
      const common = `data-meta-row="${rowIndex}" data-meta-column="${escapeHtml(column)}"`;
      if (column === groupColumn && columnIndex > 0) {
        const options = ['<option value="">未填写</option>', ...groupOptions.map((option) => `<option value="${escapeHtml(option)}" ${option === value ? 'selected' : ''}>${escapeHtml(option)}</option>`)].join('');
        return `<td><select class="table-control" ${common}>${options}</select></td>`;
      }
      const inputType = editor.types[columnIndex] === 'numeric' ? 'number' : 'text';
      return `<td><input class="table-control" type="${inputType}" ${common} value="${escapeHtml(value)}" placeholder="${column === groupColumn ? '请选择组' : '可留空'}"></td>`;
    }).join('');
    return `<tr>${cells}<td class="row-actions"><button type="button" class="table-delete" data-remove-metadata-row="${rowIndex}" title="删除这一行">删除</button></td></tr>`;
  }).join('');
  table.innerHTML = `<thead><tr>${header}<th class="actions-heading">操作</th></tr></thead><tbody>${body || '<tr><td class="table-empty" colspan="99">还没有样本行</td></tr>'}</tbody>`;
  const summary = $('metadataEditorSummary');
  if (summary) summary.textContent = `${editor.rows.length} 个样本 · ${editor.headers.length - 1} 个 metadata 列 · 空白单元格会按 QIIME2 缺失值处理`;
  const badge = $('metadataEditorBadge');
  if (badge) badge.textContent = `${editor.rows.length} samples · ${editor.headers.length} columns`;
}

function renderMetadataEditor(preview) {
  state.metadataEditor = {
    path: preview.path,
    headers: [...(preview.headers || [])],
    types: [...(preview.types || [])],
    rows: (preview.rows || []).map((row) => ({ ...row })),
    groups: [],
  };
  if (!state.metadataEditor.types.length) state.metadataEditor.types = state.metadataEditor.headers.map((_, index) => index ? 'categorical' : '');
  $('metadataEditorCard').hidden = false;
  renderMetadataGroups();
  renderMetadataTable();
}

async function loadMetadataPreview(path, silent = false) {
  if (!path) return false;
  try {
    const data = await api('/api/metadata-preview', { method: 'POST', body: JSON.stringify({ path }) });
    renderMetadataEditor(data.preview);
    return true;
  } catch (error) {
    if (!silent) toast(`metadata 预览失败：${error.message}`);
    return false;
  }
}

function addMetadataGroup() {
  const editor = state.metadataEditor;
  const input = $('groupNameInput');
  const group = input?.value.trim();
  if (!editor || !group) { toast('请输入组名，例如 control 或 treatment。'); return; }
  if (!editor.groups.includes(group)) editor.groups.push(group);
  input.value = '';
  renderMetadataGroups();
  renderMetadataTable();
}

function addMetadataColumn() {
  const editor = state.metadataEditor;
  if (!editor) return;
  const name = window.prompt('请输入新列名，例如 timepoint、site 或 treatment：', 'timepoint')?.trim();
  if (!name) return;
  if (name.toLowerCase() === 'sample-id' || editor.headers.some((header) => header.toLowerCase() === name.toLowerCase())) {
    toast('这个列名已经存在，或不能使用 sample-id。');
    return;
  }
  const type = (window.prompt('请选择列类型：categorical（分类）或 numeric（数值）', 'categorical') || 'categorical').trim().toLowerCase();
  if (!['categorical', 'numeric'].includes(type)) { toast('列类型只能是 categorical 或 numeric。'); return; }
  editor.headers.push(name);
  editor.types.push(type);
  editor.rows.forEach((row) => { row[name] = ''; });
  renderMetadataTable();
}

function removeMetadataColumn(name) {
  const editor = state.metadataEditor;
  if (!editor || editor.headers.length <= 2) { toast('至少保留一个 metadata 列，方便后续分组分析。'); return; }
  if (!window.confirm(`确定删除“${name}”这一列吗？`)) return;
  const index = editor.headers.indexOf(name);
  if (index < 1) return;
  editor.headers.splice(index, 1);
  editor.types.splice(index, 1);
  editor.rows.forEach((row) => { delete row[name]; });
  renderMetadataGroups();
  renderMetadataTable();
}

function removeMetadataGroup(group) {
  const editor = state.metadataEditor;
  if (!editor) return;
  const column = metadataGroupColumn(editor);
  editor.groups = (editor.groups || []).filter((value) => value !== group);
  editor.rows.forEach((row) => { if (row[column] === group) row[column] = ''; });
  renderMetadataGroups();
  renderMetadataTable();
}

function addMetadataRow() {
  const editor = state.metadataEditor;
  if (!editor) return;
  const row = {};
  editor.headers.forEach((header) => { row[header] = ''; });
  editor.rows.push(row);
  renderMetadataTable();
}

async function saveMetadataEditor() {
  const editor = state.metadataEditor;
  if (!editor) return;
  setBusy($('metadataSaveButton'), true, '保存中…');
  try {
    const data = await api('/api/metadata-save', { method: 'POST', body: JSON.stringify({ path: editor.path, headers: editor.headers, types: editor.types, rows: editor.rows }) });
    state.metadata = data.path;
    $('metadataPath').value = data.path;
    $('metadataName').textContent = '已保存并更新到服务器';
    renderMetadataEditor(data.preview);
    await validateMetadata(data.path);
    toast('metadata 已保存，可以继续配置分析。', true);
  } catch (error) { toast(`metadata 保存失败：${error.message}`); }
  finally { setBusy($('metadataSaveButton'), false); }
}

async function loadManifestPreview(path, silent = true) {
  if (!path || !state.scan?.data_type?.startsWith('manifest')) return false;
  try {
    const data = await api('/api/manifest-preview', { method: 'POST', body: JSON.stringify({ path }) });
    state.manifestEditor = { ...data.preview, rows: (data.preview.rows || []).map((row) => ({ ...row })) };
    renderManifestEditor();
    return true;
  } catch (error) {
    if (!silent) toast(`manifest 预览失败：${error.message}`);
    return false;
  }
}

function renderManifestEditor() {
  const editor = state.manifestEditor;
  const card = $('manifestEditorCard');
  const table = $('manifestEditorTable');
  if (!editor || !card || !table) return;
  card.hidden = false;
  $('manifestEditorBadge').textContent = `${editor.sample_count || editor.rows.length} samples · ${editor.data_type === 'manifest_paired' ? '双端' : '单端'}`;
  $('manifestEditorSummary').textContent = `${editor.rows.length} 个样本 · ${editor.fastq_count || 0} 个 FASTQ 路径 · ${editor.missing_files?.length || 0} 个路径待确认`;
  const header = editor.headers.map((column) => `<th>${escapeHtml(column)}</th>`).join('');
  const body = editor.rows.map((row, rowIndex) => {
    const status = editor.path_status?.[rowIndex]?.files || [];
    const good = status.length > 0 && status.every((item) => item.exists);
    const cells = editor.headers.map((column) => `<td><input class="table-control" data-manifest-row="${rowIndex}" data-manifest-column="${escapeHtml(column)}" value="${escapeHtml(row[column] || '')}"></td>`).join('');
    return `<tr>${cells}<td class="path-state ${good ? 'good' : 'warn'}">${good ? '✓ 已找到' : '待匹配'}</td><td class="row-actions"><button type="button" class="table-delete" data-remove-manifest-row="${rowIndex}">删除</button></td></tr>`;
  }).join('');
  table.innerHTML = `<thead><tr>${header}<th>文件状态</th><th class="actions-heading">操作</th></tr></thead><tbody>${body || '<tr><td class="table-empty" colspan="99">还没有样本行</td></tr>'}</tbody>`;
}

function addManifestRow() {
  const editor = state.manifestEditor;
  if (!editor) return;
  const row = {};
  editor.headers.forEach((header) => { row[header] = ''; });
  editor.rows.push(row);
  renderManifestEditor();
}

async function saveManifestEditor() {
  const editor = state.manifestEditor;
  if (!editor) return;
  setBusy($('manifestSaveButton'), true, '保存中…');
  try {
    const data = await api('/api/manifest-save', { method: 'POST', body: JSON.stringify({ path: editor.path, data_type: editor.data_type, rows: editor.rows }) });
    showScan(data.scan, false);
    state.manifestEditor = data.preview;
    renderManifestEditor();
    toast('manifest 已保存，序列文件识别结果已刷新。', true);
  } catch (error) { toast(`manifest 保存失败：${error.message}`); }
  finally { setBusy($('manifestSaveButton'), false); }
}

async function validateMetadata(path, silent = false) {
  if (!path) { state.metadataValid = false; updateReadiness(); return false; }
  try {
    const data = await api('/api/validate-metadata', { method: 'POST', body: JSON.stringify({ path }) });
    state.metadataValid = Boolean(data.validation?.valid);
    const validation = data.validation || {};
    $('metadataValidation').textContent = state.metadataValid
      ? `✓ 已通过 · ${validation.sample_count || 0} 个样本 · ${validation.columns?.length || 0} 列${validation.warnings?.length ? ` · ${validation.warnings.length} 个空值按缺失处理` : ''}`
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

function updateFigaroStatus(environment = state.selectedEnvironment) {
  const button = $('figaroInstallButton');
  if (!button) return;
  if (!environment) {
    $('figaroValue').textContent = 'Figaro 待检测';
    $('figaroDesc').textContent = '选中 QIIME2 环境后，程序会检查 Figaro。';
    button.disabled = true;
    return;
  }
  if (environment.figaro_available) {
    $('figaroValue').textContent = `Figaro READY${environment.figaro_version ? ` · ${environment.figaro_version}` : ''}`;
    $('figaroDesc').textContent = '当前环境可以自动估算截断长度。';
    button.disabled = true;
    button.textContent = '已安装';
  } else {
    $('figaroValue').textContent = 'Figaro 未安装';
    $('figaroDesc').textContent = state.platform === 'posix' && state.condaAvailable ? '可以一键安装到当前选中的 Conda 环境。' : '目标服务器需要 Linux + Conda 才能一键安装。';
    button.disabled = !(state.platform === 'posix' && state.condaAvailable);
    button.textContent = '一键安装 Figaro';
  }
}

async function loadEnvironments() {
  const select = $('environmentSelect');
  select.innerHTML = '<option value="">正在执行 conda env list…</option>';
  $('refreshEnvironments').disabled = true;
  try {
    const data = await api('/api/environments');
    state.condaAvailable = Boolean(data.conda_available);
    state.environments = data.environments || [];
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
      state.selectedEnvironment = active;
      state.qiimeEnv = active.name;
      select.value = active.name;
      $('environmentStatus').textContent = `✓ 已选中 ${environmentLabel(active)}，点击底部按钮即可运行。`;
      $('environmentStatus').className = 'environment-status good';
      $('qiimeValue').textContent = 'READY';
      $('qiimeDesc').textContent = `已找到 ${usable.length} 个可用的 QIIME2 环境。`;
      $('installHelper').hidden = true;
      markStep('runtime', 'done', '已就绪');
      updateFigaroStatus(active);
    } else {
      state.qiimeEnv = '';
      state.selectedEnvironment = null;
      $('environmentStatus').textContent = data.error || (state.condaAvailable ? '找到了 Conda，但没有环境包含 QIIME2。' : '没有找到 Conda，请在 Linux 服务器检查安装。');
      $('environmentStatus').className = 'environment-status warn';
      $('qiimeValue').textContent = 'NEEDS INSTALL';
      $('qiimeDesc').textContent = state.condaAvailable ? 'Conda 存在，但当前环境都没有 QIIME2。' : '当前服务没有找到 Conda。';
      $('installHelper').hidden = false;
      markStep('runtime', 'blocked', '需要安装');
      await loadInstallOptions();
      updateFigaroStatus(null);
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
    updateFigaroStatus(null);
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

function setClassifier(path, label = '已选择分类器') {
  state.classifier = path || '';
  $('classifierPath').value = state.classifier;
  $('classifierName').textContent = state.classifier ? `${label} · ${state.classifier}` : '未选择分类器';
  updateReadiness();
}

function renderClassifierCatalog() {
  const target = $('classifierCatalog');
  if (!target) return;
  if (!state.classifierCatalog.length) {
    target.innerHTML = '<div class="catalog-loading">暂时没有可用的官方分类器。</div>';
    return;
  }
  target.innerHTML = state.classifierCatalog.map((item) => `<div class="catalog-card ${item.downloaded ? 'downloaded' : ''}"><div><div class="catalog-title"><strong>${escapeHtml(item.name)}</strong>${item.recommended ? '<span class="catalog-recommended">推荐</span>' : ''}</div><small>${escapeHtml(item.description)}</small><code>${escapeHtml(item.filename)}</code></div><button type="button" class="subtle-button classifier-action" data-classifier-id="${escapeHtml(item.id)}" data-downloaded="${item.downloaded}">${item.downloaded ? (state.classifier === item.path ? '当前使用' : '使用此分类器') : '下载到项目'}</button></div>`).join('');
}

async function loadClassifiers() {
  try {
    const data = await api('/api/classifiers');
    state.classifierCatalog = data.catalog || [];
    if (!state.classifier && data.default) setClassifier(data.default, '默认分类器');
    renderClassifierCatalog();
  } catch (error) {
    $('classifierCatalog').innerHTML = `<div class="catalog-loading">官方分类器读取失败：${escapeHtml(error.message)}</div>`;
  }
}

async function downloadClassifier(id, button) {
  const item = state.classifierCatalog.find((value) => value.id === id);
  if (!item) return;
  if (item.downloaded) { setClassifier(item.path, '已选择项目内分类器'); renderClassifierCatalog(); return; }
  if (button) { button.disabled = true; button.innerHTML = '<span class="button-spinner"></span>准备下载…'; }
  try {
    const started = await api('/api/classifiers/download', { method: 'POST', body: JSON.stringify({ id }) });
    const poll = async () => {
      const current = await api(`/api/download-jobs/${started.job_id}`);
      const percent = Number(current.job.percent || 0);
      if (button) button.textContent = current.job.status === 'running' ? `下载中 ${percent}%` : '下载完成';
      if (current.job.status === 'running') { window.setTimeout(poll, 800); return; }
      if (current.job.status !== 'completed') throw new Error(current.job.error || '分类器下载失败');
      setClassifier(current.job.path, '已下载到项目文件夹');
      await loadClassifiers();
      toast('官方分类器已下载，并已设为当前分类器。', true);
    };
    await poll();
  } catch (error) {
    if (button) { button.disabled = false; button.textContent = item.downloaded ? '使用此分类器' : '下载到项目'; }
    toast(error.message);
  }
}

async function installFigaro() {
  if (!state.selectedEnvironment?.name) { toast('请先选择一个 QIIME2 Conda 环境。'); return; }
  setBusy($('figaroInstallButton'), true, '安装中…');
  $('figaroDesc').textContent = '正在把 Figaro 安装到当前选中的环境，请耐心等待。';
  try {
    const started = await api('/api/figaro/install', { method: 'POST', body: JSON.stringify({ environment_name: state.selectedEnvironment.name }) });
    const poll = async () => {
      const current = await api(`/api/install-jobs/${started.job_id}`);
      $('figaroDesc').textContent = current.job.output || current.job.status;
      if (current.job.status === 'running') { window.setTimeout(poll, 1200); return; }
      setBusy($('figaroInstallButton'), false);
      await loadEnvironments();
      toast(current.job.status === 'completed' ? 'Figaro 安装完成，后续分析会自动使用它。' : 'Figaro 安装失败，请查看提示信息。', current.job.status === 'completed');
    };
    window.setTimeout(poll, 600);
  } catch (error) {
    setBusy($('figaroInstallButton'), false);
    toast(error.message);
  }
}

function closeDirectoryPicker() {
  $('directoryModal').hidden = true;
}

async function loadDirectories(path = '') {
  try {
    const data = await api(`/api/directories?path=${encodeURIComponent(path)}`);
    state.directory = data;
    $('directoryModal').hidden = false;
    $('directoryPathInput').value = data.current || '';
    $('directoryCurrentLabel').textContent = data.current || '';
    $('directoryParentButton').disabled = !data.parent;
    $('directoryList').innerHTML = (data.directories || []).length
      ? data.directories.map((directory) => `<button type="button" class="directory-entry" data-directory-path="${escapeHtml(directory.path)}"><span>▸</span><strong>${escapeHtml(directory.name)}</strong></button>`).join('')
      : '<div class="directory-empty">当前目录没有可进入的子目录。</div>';
  } catch (error) { toast(`读取目录失败：${error.message}`); }
}

function chooseCurrentDirectory() {
  const value = state.directory?.current || readValue('directoryPathInput');
  if (value) $('outputPath').value = value;
  closeDirectoryPicker();
  updateReadiness();
  toast('输出目录已选择。', true);
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
  bind('manifestAddRowButton', 'click', addManifestRow);
  bind('manifestPreviewButton', 'click', () => loadManifestPreview(state.manifestEditor?.path || state.inputPath, false));
  bind('manifestSaveButton', 'click', saveManifestEditor);
  bind('metadataAddColumnButton', 'click', addMetadataColumn);
  bind('metadataPreviewButton', 'click', () => loadMetadataPreview(state.metadata || readValue('metadataPath'), false));
  bind('metadataValidateButton', 'click', () => validateMetadata(state.metadata || readValue('metadataPath')));
  bind('metadataSaveButton', 'click', saveMetadataEditor);
  bind('addGroupButton', 'click', addMetadataGroup);
  bind('groupNameInput', 'keydown', (event) => { if (event.key === 'Enter') addMetadataGroup(); });
  bind('runButton', 'click', runAnalysis);
  bind('refreshEnvironments', 'click', loadEnvironments);
  bind('environmentSelect', 'change', (event) => {
    state.qiimeEnv = event.target.value;
    state.selectedEnvironment = state.environments.find((environment) => environment.name === state.qiimeEnv) || null;
    state.environmentReady = Boolean(state.selectedEnvironment?.qiime_available);
    $('environmentStatus').textContent = state.qiimeEnv ? `✓ 已选择 ${state.qiimeEnv}` : '请选择包含 QIIME2 的环境';
    $('environmentStatus').className = `environment-status ${state.qiimeEnv ? 'good' : 'warn'}`;
    updateFigaroStatus(state.selectedEnvironment);
    updateReadiness();
  });
  bind('installVersion', 'change', updateInstallSummary);
  bind('installDistribution', 'change', updateInstallSummary);
  bind('installEnvironmentName', 'input', updateInstallSummary);
  bind('installButton', 'click', installQiime);
  bind('figaroInstallButton', 'click', installFigaro);
  bind('metadataPath', 'input', () => { state.metadata = readValue('metadataPath'); state.metadataValid = false; $('metadataName').textContent = state.metadata ? '等待校验服务器路径' : '未选择 metadata'; updateReadiness(); });
  bind('metadataPath', 'blur', () => validateMetadata(readValue('metadataPath'), true));
  bind('classifierPath', 'input', () => { state.classifier = readValue('classifierPath'); $('classifierName').textContent = state.classifier ? '使用服务器路径' : '未选择分类器'; updateReadiness(); });
  bind('outputPickerButton', 'click', () => loadDirectories(readValue('outputPath')));
  bind('directoryCloseButton', 'click', closeDirectoryPicker);
  bind('directoryGoButton', 'click', () => loadDirectories(readValue('directoryPathInput')));
  bind('directoryPathInput', 'keydown', (event) => { if (event.key === 'Enter') loadDirectories(readValue('directoryPathInput')); });
  bind('directoryParentButton', 'click', () => loadDirectories(state.directory?.parent || ''));
  bind('directoryChooseButton', 'click', chooseCurrentDirectory);
  $('classifierCatalog')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-classifier-id]');
    if (button) downloadClassifier(button.dataset.classifierId, button);
  });
  $('groupList')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-remove-group]');
    if (button) removeMetadataGroup(button.dataset.removeGroup);
  });
  $('metadataEditorTable')?.addEventListener('input', (event) => {
    const target = event.target.closest('[data-meta-row][data-meta-column]');
    if (!target || !state.metadataEditor) return;
    state.metadataEditor.rows[Number(target.dataset.metaRow)][target.dataset.metaColumn] = target.value;
    state.metadataValid = false;
    updateReadiness();
  });
  $('metadataEditorTable')?.addEventListener('change', (event) => {
    const target = event.target.closest('[data-meta-row][data-meta-column]');
    if (!target || !state.metadataEditor) return;
    state.metadataEditor.rows[Number(target.dataset.metaRow)][target.dataset.metaColumn] = target.value;
    if (target.dataset.metaColumn === metadataGroupColumn()) renderMetadataGroups();
    state.metadataValid = false;
    updateReadiness();
  });
  $('metadataEditorTable')?.addEventListener('click', (event) => {
    const column = event.target.closest('[data-remove-column]')?.dataset.removeColumn;
    if (column) removeMetadataColumn(column);
    const row = event.target.closest('[data-remove-metadata-row]')?.dataset.removeMetadataRow;
    if (row !== undefined && window.confirm('确定删除这个样本吗？')) { state.metadataEditor.rows.splice(Number(row), 1); renderMetadataGroups(); renderMetadataTable(); state.metadataValid = false; updateReadiness(); }
  });
  $('manifestEditorTable')?.addEventListener('input', (event) => {
    const target = event.target.closest('[data-manifest-row][data-manifest-column]');
    if (target && state.manifestEditor) state.manifestEditor.rows[Number(target.dataset.manifestRow)][target.dataset.manifestColumn] = target.value;
  });
  $('manifestEditorTable')?.addEventListener('click', (event) => {
    const row = event.target.closest('[data-remove-manifest-row]')?.dataset.removeManifestRow;
    if (row !== undefined && window.confirm('确定删除这个 manifest 样本吗？')) { state.manifestEditor.rows.splice(Number(row), 1); renderManifestEditor(); }
  });
  $('directoryList')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-directory-path]');
    if (button) loadDirectories(button.dataset.directoryPath);
  });
  $('directoryModal')?.addEventListener('click', (event) => { if (event.target.id === 'directoryModal') closeDirectoryPicker(); });
  $$('input[name="samplingMode"]').forEach((input) => input.addEventListener('change', updateSamplingMode));
  ['samplingDepth', 'skipTaxonomy', 'noTrim', 'noFilter', 'noFigaro', 'skipDiversity', 'skipAncom'].forEach((id) => bind(id, 'input', updateReadiness));
  loadClassifiers();
  checkHealth();
});
