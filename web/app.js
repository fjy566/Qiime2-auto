const state = {
  scan: null,
  inputPath: '',
  uploadSession: '',
  selectedNames: [],
  uploadedFastqPaths: [],
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
  preflight: null,
  preflightTimer: null,
  preflightRequest: 0,
  ancomChoice: null,
  directory: null,
  busy: false,
  jobId: '',
};

const METADATA_COLUMN_TEMPLATES = [
  { name: 'group', type: 'categorical', label: '实验分组', hint: 'control / treatment', icon: '◫' },
  { name: 'subject-id', type: 'categorical', label: '受试者', hint: '同一对象的重复采样', icon: '◎' },
  { name: 'timepoint', type: 'categorical', label: '时间点', hint: 'day-0 / day-7', icon: '◷' },
  { name: 'body-site', type: 'categorical', label: '采样部位', hint: 'gut / oral / skin', icon: '⌖' },
  { name: 'treatment', type: 'categorical', label: '处理方式', hint: '药物、剂量或条件', icon: '✦' },
  { name: 'batch', type: 'categorical', label: '实验批次', hint: '排查批次效应', icon: '▦' },
  { name: 'replicate', type: 'categorical', label: '重复编号', hint: '生物或技术重复', icon: '⧉' },
  { name: 'age', type: 'numeric', label: '年龄', hint: '连续数值', icon: '∿' },
  { name: 'ph', type: 'numeric', label: 'pH', hint: '连续测量值', icon: '◇' },
  { name: 'temperature', type: 'numeric', label: '温度', hint: '连续测量值', icon: '⌁' },
];

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
    sampling_depth: diversityWillBeSkipped() || selectedSamplingMode() === 'auto' ? 'auto' : readValue('samplingDepth'),
    qiime_env: state.qiimeEnv,
    qiime_env_available: state.selectedEnvironment ? Boolean(state.selectedEnvironment.qiime_available) : undefined,
    biom_available: state.selectedEnvironment ? Boolean(state.selectedEnvironment.biom_available) : undefined,
    no_trim: isChecked('noTrim'),
    no_filter: isChecked('noFilter'),
    no_figaro: isChecked('noFigaro'),
    skip_taxonomy: isChecked('skipTaxonomy'),
    skip_diversity: isChecked('skipDiversity'),
    skip_ancom: $('skipAncom')?.dataset.autoDisabled === 'true' ? Boolean(state.ancomChoice) : isChecked('skipAncom'),
    platform: state.platform,
    figaro_available: state.selectedEnvironment ? Boolean(state.selectedEnvironment.figaro_available) : undefined,
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
  state.preflight = null;
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
    if (kind === 'fastq') state.uploadedFastqPaths = [...new Set([...state.uploadedFastqPaths, ...(result.files || [])])];
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
    ? editor.groups.map((group, index) => {
      const count = editor.rows.filter((row) => row[column] === group).length;
      return `<div class="group-chip" style="--chip-index:${index}"><span><i></i>${escapeHtml(group)} <small>${count} 个样本</small></span><button type="button" data-remove-group="${escapeHtml(group)}" aria-label="删除 ${escapeHtml(group)}">×</button></div>`;
    }).join('')
    : '<span class="group-empty">还没有组；先新建 control、treatment 等组</span>';
}

function renderGroupSamplePicker() {
  const target = $('groupSamplePicker');
  const editor = state.metadataEditor;
  if (!target || !editor) return;
  const idColumn = editor.headers[0];
  const groupColumn = metadataGroupColumn(editor);
  const query = readValue('groupSampleSearch').toLowerCase();
  const visible = editor.rows.map((row, index) => ({ row, index })).filter(({ row }) => !query || String(row[idColumn] || '').toLowerCase().includes(query));
  target.innerHTML = visible.length ? visible.map(({ row, index }) => {
    const sampleId = row[idColumn] || `第 ${index + 1} 行（未填写 ID）`;
    const group = row[groupColumn] || '未分组';
    const checked = editor.selectedSamples?.has(index) ? 'checked' : '';
    return `<label class="sample-choice ${group === '未分组' ? 'unassigned' : ''}"><input type="checkbox" data-group-sample="${index}" ${checked}><span class="sample-check">✓</span><span><strong>${escapeHtml(sampleId)}</strong><small>${escapeHtml(group)}</small></span></label>`;
  }).join('') : '<div class="sample-picker-empty">没有匹配的样本</div>';
}

function renderMetadataColumnTemplates() {
  const target = $('metadataColumnTemplates');
  const editor = state.metadataEditor;
  if (!target || !editor) return;
  target.innerHTML = METADATA_COLUMN_TEMPLATES.map((template) => {
    const exists = editor.headers.some((header) => header.toLowerCase() === template.name);
    return `<button type="button" class="column-template ${exists ? 'added' : ''}" data-column-template="${template.name}" ${exists ? 'disabled' : ''}><span>${template.icon}</span><div><strong>${template.label}</strong><small>${template.name} · ${template.hint}</small></div><b>${exists ? '已添加' : '+'}</b></button>`;
  }).join('');
}

function renderMetadataTable() {
  const table = $('metadataEditorTable');
  const editor = state.metadataEditor;
  if (!table || !editor) return;
  const groupColumn = metadataGroupColumn(editor);
  const groupOptions = [...new Set([...(editor.groups || []), ...editor.rows.map((row) => row[groupColumn] || '').filter(Boolean)])];
  const header = editor.headers.map((column, index) => `<th><div>${escapeHtml(column)}${index ? `<select class="column-type-select" data-column-type="${index}"><option value="categorical" ${editor.types[index] === 'categorical' ? 'selected' : ''}>分类</option><option value="numeric" ${editor.types[index] === 'numeric' ? 'selected' : ''}>数值</option></select>` : '<small>样本 ID</small>'}</div>${index ? `<button type="button" class="table-delete" data-remove-column="${escapeHtml(column)}" title="删除这一列">×</button>` : ''}</th>`).join('');
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
  renderGroupSamplePicker();
  renderMetadataColumnTemplates();
}

function renderMetadataEditor(preview) {
  state.metadataEditor = {
    path: preview.path,
    headers: [...(preview.headers || [])],
    types: [...(preview.types || [])],
    rows: (preview.rows || []).map((row) => ({ ...row })),
    groups: [],
    selectedSamples: new Set(),
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
  if (!editor || !group) { toast('先输入组名，例如 control 或 treatment。'); return; }
  if (!editor.selectedSamples?.size) { toast('还没有选择样本。请勾选属于这个组的样本。'); return; }
  let column = metadataGroupColumn(editor);
  if (!editor.headers.some((header) => header === column)) {
    addMetadataColumn('group', 'categorical');
    column = 'group';
  }
  if (!editor.groups.includes(group)) editor.groups.push(group);
  editor.selectedSamples.forEach((index) => { if (editor.rows[index]) editor.rows[index][column] = group; });
  const assignedCount = editor.selectedSamples.size;
  editor.selectedSamples.clear();
  input.value = '';
  renderMetadataGroups();
  renderMetadataTable();
  markMetadataChanged();
  toast(`已把 ${assignedCount} 个样本分到 ${group}。`, true);
}

function addMetadataColumn(name, type = 'categorical') {
  const editor = state.metadataEditor;
  if (!editor) return;
  if (!name) { $('customColumnForm').hidden = !$('customColumnForm').hidden; if (!$('customColumnForm').hidden) $('customColumnName').focus(); return; }
  name = String(name).trim();
  if (name.toLowerCase() === 'sample-id' || editor.headers.some((header) => header.toLowerCase() === name.toLowerCase())) {
    toast('这个列名已经存在，或不能使用 sample-id。');
    return false;
  }
  type = String(type).trim().toLowerCase();
  if (!['categorical', 'numeric'].includes(type)) { toast('列类型只能是 categorical 或 numeric。'); return false; }
  editor.headers.push(name);
  editor.types.push(type);
  editor.rows.forEach((row) => { row[name] = ''; });
  renderMetadataTable();
  markMetadataChanged();
  return true;
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
  markMetadataChanged();
}

function removeMetadataGroup(group) {
  const editor = state.metadataEditor;
  if (!editor) return;
  const column = metadataGroupColumn(editor);
  editor.groups = (editor.groups || []).filter((value) => value !== group);
  editor.rows.forEach((row) => { if (row[column] === group) row[column] = ''; });
  renderMetadataGroups();
  renderMetadataTable();
  markMetadataChanged();
}

function addMetadataRow() {
  const editor = state.metadataEditor;
  if (!editor) return;
  const row = {};
  editor.headers.forEach((header) => { row[header] = ''; });
  editor.rows.push(row);
  renderMetadataTable();
  markMetadataChanged();
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
  if (!path) return false;
  try {
    const data = await api('/api/manifest-preview', { method: 'POST', body: JSON.stringify({ path }) });
    state.manifestEditor = { ...data.preview, isNew: false, rows: (data.preview.rows || []).map((row) => ({ ...row })) };
    $('manifestTypeSelect').value = state.manifestEditor.data_type;
    $('manifestSavePath').value = state.manifestEditor.path;
    renderManifestEditor();
    return true;
  } catch (error) {
    if (!silent) toast(`manifest 预览失败：${error.message}`);
    return false;
  }
}

function manifestHeaders(dataType) {
  return dataType === 'manifest_single'
    ? ['sample-id', 'absolute-filepath']
    : ['sample-id', 'forward-absolute-filepath', 'reverse-absolute-filepath'];
}

function sampleNameFromFastq(path) {
  const filename = String(path).split(/[\\/]/).pop() || '';
  return filename.replace(/\.(fastq|fq)(\.gz)?$/i, '').replace(/([._-])R?[12]([._-]?\d{3})?$/i, '').replace(/([._-])read[12]$/i, '') || 'sample';
}

function createManifestEditor() {
  const dataType = $('manifestTypeSelect')?.value || 'manifest_paired';
  const headers = manifestHeaders(dataType);
  const paths = state.uploadedFastqPaths;
  const rowsBySample = new Map();
  paths.forEach((path) => {
    const sample = sampleNameFromFastq(path);
    if (!rowsBySample.has(sample)) rowsBySample.set(sample, { 'sample-id': sample });
    const row = rowsBySample.get(sample);
    if (dataType === 'manifest_single') row['absolute-filepath'] ||= path;
    else if (/(^|[._-])R?2([._-]|\d|$)/i.test(path) || /read2/i.test(path)) row['reverse-absolute-filepath'] = path;
    else row['forward-absolute-filepath'] ||= path;
  });
  const rows = [...rowsBySample.values()];
  if (!rows.length) rows.push(Object.fromEntries(headers.map((header) => [header, ''])));
  rows.forEach((row) => headers.forEach((header) => { row[header] ||= ''; }));
  state.manifestEditor = { path: readValue('manifestSavePath', 'prepared/manifest.tsv'), isNew: true, data_type: dataType, headers, rows, sample_count: rows.length, fastq_count: paths.length, missing_files: [], path_status: [] };
  renderManifestEditor();
  $('manifestEditorCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
  toast(paths.length ? `已把选择过的 ${paths.length} 个 FASTQ 带入编辑器，请确认配对。` : '空白 manifest 已建立，请填写第一个样本。', true);
}

function renderManifestEditor() {
  const editor = state.manifestEditor;
  const card = $('manifestEditorCard');
  const table = $('manifestEditorTable');
  if (!editor || !card || !table) return;
  card.hidden = false;
  $('manifestTypeSelect').value = editor.data_type;
  if (!$('manifestSavePath').value) $('manifestSavePath').value = editor.path || 'prepared/manifest.tsv';
  $('manifestEditorBadge').textContent = `${editor.rows.length} samples · ${editor.data_type === 'manifest_paired' ? '双端' : '单端'}`;
  const requiredPaths = editor.rows.length * (editor.headers.length - 1);
  const filledPaths = editor.rows.reduce((count, row) => count + editor.headers.slice(1).filter((column) => row[column]).length, 0);
  $('manifestEditorSummary').innerHTML = `<strong>${editor.rows.length}</strong> 个样本 <i></i><strong>${filledPaths}/${requiredPaths}</strong> 个 FASTQ 路径已填写 <i></i><span>${filledPaths === requiredPaths ? '可以保存' : '请补齐空白路径'}</span>`;
  const header = editor.headers.map((column) => `<th>${escapeHtml(column)}</th>`).join('');
  const body = editor.rows.map((row, rowIndex) => {
    const status = editor.path_status?.[rowIndex]?.files || [];
    const good = status.length > 0 && status.every((item) => item.exists);
    const complete = editor.headers.slice(1).every((column) => row[column]);
    const cells = editor.headers.map((column, columnIndex) => `<td><input class="table-control ${!row[column] ? 'is-empty' : ''}" data-manifest-row="${rowIndex}" data-manifest-column="${escapeHtml(column)}" value="${escapeHtml(row[column] || '')}" placeholder="${columnIndex ? (column.includes('reverse') ? '/data/sample_R2.fastq.gz' : '/data/sample_R1.fastq.gz') : `sample-${rowIndex + 1}`}"></td>`).join('');
    const statusLabel = good ? '✓ 已找到' : status.length ? '路径待确认' : complete ? '保存后检查' : '需要补全';
    return `<tr>${cells}<td class="path-state ${good ? 'good' : 'warn'}">${statusLabel}</td><td class="row-actions"><button type="button" class="table-delete" data-remove-manifest-row="${rowIndex}">删除</button></td></tr>`;
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

function changeManifestType(dataType) {
  const editor = state.manifestEditor;
  if (!editor) return;
  const previousHeaders = editor.headers;
  const headers = manifestHeaders(dataType);
  editor.rows = editor.rows.map((row) => {
    const next = { 'sample-id': row['sample-id'] || '' };
    if (dataType === 'manifest_single') next['absolute-filepath'] = row['absolute-filepath'] || row['forward-absolute-filepath'] || '';
    else {
      next['forward-absolute-filepath'] = row['forward-absolute-filepath'] || row['absolute-filepath'] || '';
      next['reverse-absolute-filepath'] = row['reverse-absolute-filepath'] || '';
    }
    return next;
  });
  editor.data_type = dataType;
  editor.headers = headers;
  editor.path_status = [];
  editor.missing_files = [];
  renderManifestEditor();
  if (previousHeaders.length !== headers.length) toast(dataType === 'manifest_paired' ? '已切换为双端，请为每个样本补齐 R2。' : '已切换为单端，将使用原来的 R1 路径。');
}

async function saveManifestEditor() {
  const editor = state.manifestEditor;
  if (!editor) return;
  setBusy($('manifestSaveButton'), true, '保存中…');
  try {
    const path = readValue('manifestSavePath', editor.path || 'prepared/manifest.tsv');
    const data = await api('/api/manifest-save', { method: 'POST', body: JSON.stringify({ path, data_type: editor.data_type, rows: editor.rows }) });
    showScan(data.scan, false);
    state.manifestEditor = { ...data.preview, isNew: false };
    state.inputPath = data.path;
    $('inputPath').value = data.path;
    $('manifestSavePath').value = data.path;
    renderManifestEditor();
    toast('manifest 已保存并设为本次分析输入。', true);
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
  schedulePreflight();
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
      $('environmentStatus').textContent = `✓ 已选中 ${environmentLabel(active)}；${active.biom_available ? 'biom 可用' : '未找到 biom，自动采样深度会跳过'}`;
      $('environmentStatus').className = 'environment-status good';
      $('qiimeValue').textContent = 'READY';
      $('qiimeDesc').textContent = `已找到 ${usable.length} 个可用的 QIIME2 环境；${active.biom_available ? 'biom 可用' : '未找到 biom，自动采样深度会跳过'}。`;
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
  state.preflight = null;
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
  const disabled = isChecked('skipTaxonomy') ? ' disabled' : '';
  target.innerHTML = state.classifierCatalog.map((item) => `<div class="catalog-card ${item.downloaded ? 'downloaded' : ''}"><div><div class="catalog-title"><strong>${escapeHtml(item.name)}</strong>${item.recommended ? '<span class="catalog-recommended">推荐</span>' : ''}</div><small>${escapeHtml(item.description)}</small><code>${escapeHtml(item.filename)}</code></div><button type="button" class="subtle-button classifier-action" data-classifier-id="${escapeHtml(item.id)}" data-downloaded="${item.downloaded}"${disabled}>${item.downloaded ? (state.classifier === item.path ? '当前使用' : '使用此分类器') : '下载到项目'}</button></div>`).join('');
  updateOptionDependencies();
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
  if (isChecked('skipTaxonomy')) { toast('已跳过物种分类，先取消勾选后再选择分类器。'); return; }
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

function metadataPath() { return state.metadata || readValue('metadataPath'); }

function markMetadataChanged() {
  state.metadataValid = false;
  state.preflight = null;
  updateMetadataCapability();
  updateReadiness();
}

function diversityWillBeSkipped() {
  if (isChecked('skipDiversity')) return true;
  if (state.preflight) return Boolean(state.preflight.effective?.skip_diversity);
  return !Boolean(metadataPath());
}

function updateMetadataCapability(metadata = state.preflight?.metadata) {
  const box = $('metadataCapability');
  if (!box) return;
  const provided = Boolean(metadata?.provided || metadataPath());
  const valid = Boolean(metadata?.usable ?? state.metadataValid);
  const groupReady = Boolean(metadata?.group_ready);
  box.className = `capability-note ${!provided ? 'info' : valid && groupReady ? 'good' : valid ? 'warn' : 'warn'}`;
  if (!provided) {
    box.innerHTML = '<strong>可以先不提供</strong><span>没有 metadata 时仍可做数据导入、DADA2 和基础物种分类；多样性与差异分析会自动跳过。</span>';
  } else if (!valid) {
    box.innerHTML = '<strong>metadata 还不能使用</strong><span>文件仍可保存，但本次会跳过依赖 metadata 的多样性与差异分析；修正后可重新运行。</span>';
  } else if (!groupReady) {
    box.innerHTML = `<strong>metadata 已通过，但没有可用分组</strong><span>${escapeHtml(metadata?.group_message || 'ANCOM 会自动跳过；多样性分析仍可执行。')}</span>`;
  } else {
    box.innerHTML = `<strong>metadata 已准备好</strong><span>${escapeHtml(metadata?.group_message || '可用于多样性和分组差异分析。')}</span>`;
  }
}

function updateOptionDependencies() {
  const skipTaxonomy = isChecked('skipTaxonomy');
  const classifierControls = $('classifierControls');
  classifierControls?.classList.toggle('dependency-disabled', skipTaxonomy);
  ['classifierPicker', 'classifierPath'].forEach((id) => { if ($(id)) $(id).disabled = skipTaxonomy; });
  $('classifierCatalog')?.querySelectorAll('button').forEach((button) => { button.disabled = skipTaxonomy; });
  const classifierNote = $('classifierDependencyNote');
  if (classifierNote) {
    classifierNote.className = `capability-note ${skipTaxonomy ? 'info' : 'warn'}`;
    classifierNote.innerHTML = skipTaxonomy
      ? '<strong>物种分类已跳过</strong><span>分类器、官方分类器下载和本次 taxonomy 相关步骤都不会执行。</span>'
      : '<strong>需要分类器</strong><span>勾选下方“跳过物种分类”后，这一整块会变灰并暂时不可操作。</span>';
  }

  const skipDiversity = diversityWillBeSkipped();
  const samplingGroup = $('samplingGroup');
  samplingGroup?.classList.toggle('dependency-disabled', skipDiversity);
  $$('input[name="samplingMode"]').forEach((input) => { input.disabled = skipDiversity; });
  if ($('samplingDepth')) $('samplingDepth').disabled = skipDiversity || selectedSamplingMode() !== 'custom';
  if (skipDiversity && $('samplingDepthHelp')) {
    $('samplingDepthHelp').textContent = isChecked('skipDiversity')
      ? '已跳过多样性分析，本次不需要采样深度。取消勾选后可以选择自动推荐或自定义。'
      : '没有可用 metadata，多样性分析会自动跳过，因此本次不需要采样深度。补齐 metadata 后即可恢复。';
    $('samplingDepthHelp').className = 'field-help';
  }

  const checkbox = $('skipAncom');
  if (checkbox) {
    if (state.ancomChoice === null) state.ancomChoice = checkbox.checked;
    const metadata = state.preflight?.metadata;
    const ancomAvailable = !skipTaxonomy && Boolean(metadata?.usable && metadata?.group_ready);
    if (!ancomAvailable) {
      if (checkbox.dataset.autoDisabled !== 'true') state.ancomChoice = checkbox.checked;
      checkbox.checked = true;
      checkbox.disabled = true;
      checkbox.dataset.autoDisabled = 'true';
      $('skipAncomHelp').textContent = skipTaxonomy ? '需要 taxonomy；已自动跳过' : '需要完整的分类分组列；已自动跳过';
    } else {
      if (checkbox.dataset.autoDisabled === 'true') checkbox.checked = Boolean(state.ancomChoice);
      checkbox.disabled = false;
      delete checkbox.dataset.autoDisabled;
      $('skipAncomHelp').textContent = '不做差异分析';
    }
  }
}

function renderPreflightPlan(plan) {
  const blockers = plan?.blockers || [];
  const blockedIds = new Set(blockers.map((item) => item.id));
  const planItems = [
    ...blockers.map((item) => ({ id: item.id, label: item.title, status: 'blocked', message: item.message })),
    ...(plan?.steps || []).filter((step) => step.status === 'skipped' || (step.status === 'blocked' && !blockedIds.has(step.id))),
  ];
  const warnings = plan?.warnings || [];
  const notice = $('planNotice');
  const detail = $('preflightPlan');
  if (notice) {
    notice.hidden = !(warnings.length || planItems.length);
    const title = blockers.length ? '还不能开始分析' : '这次不会执行全部步骤';
    notice.innerHTML = notice.hidden ? '' : `<div class="plan-notice-title"><span>◌</span><strong>${title}</strong></div><div class="plan-notice-copy">${escapeHtml(blockers[0]?.message || warnings[0]?.message || planItems[0]?.message || '页面会按当前选项执行可用步骤。')}</div>`;
  }
  if (detail) {
    detail.hidden = !(warnings.length || planItems.length);
    if (!detail.hidden) {
      const items = planItems.map((step) => `<div class="preflight-item ${escapeHtml(step.status)}"><span>${step.status === 'blocked' ? '!' : '→'}</span><div><strong>${escapeHtml(step.label)}</strong><small>${escapeHtml(step.message)}</small></div></div>`).join('');
      const summary = blockers.length
        ? `${blockers.length} 个阻塞项${warnings.length ? ` · ${warnings.length} 条提示` : ''}`
        : warnings.length ? `${warnings.length} 条提示` : '已自动整理';
      detail.innerHTML = `<div class="preflight-title"><span>本次分析计划</span><small>${summary}</small></div>${items}`;
    }
  }
}

function renderPreflight(plan) {
  state.preflight = plan;
  updateMetadataCapability(plan?.metadata);
  updateOptionDependencies();
  renderPreflightPlan(plan);
  updateReadiness(false);
}

function schedulePreflight() {
  window.clearTimeout(state.preflightTimer);
  const requestId = ++state.preflightRequest;
  state.preflightTimer = window.setTimeout(async () => {
    try {
      const result = await api('/api/preflight', { method: 'POST', body: JSON.stringify(configPayload()) });
      if (requestId === state.preflightRequest) renderPreflight(result.preflight);
    } catch {
      updateMetadataCapability();
      updateOptionDependencies();
    }
  }, 180);
}

function samplingIsValid() {
  if (diversityWillBeSkipped() || selectedSamplingMode() === 'auto') return true;
  const value = Number(readValue('samplingDepth'));
  return Number.isInteger(value) && value > 0;
}

function updateSamplingMode() {
  const custom = selectedSamplingMode() === 'custom';
  const skipDiversity = diversityWillBeSkipped();
  updateOptionDependencies();
  $('samplingDepth').disabled = skipDiversity || !custom;
  $('samplingDepth').setAttribute('aria-disabled', String(skipDiversity || !custom));
  $('samplingDepthHelp').textContent = skipDiversity
    ? (isChecked('skipDiversity') ? '已跳过多样性分析，本次不需要采样深度。' : '没有可用 metadata，多样性分析会自动跳过，因此本次不需要采样深度。')
    : custom
    ? '自定义值会直接用于多样性分析；建议不要高于大多数样本的测序深度。'
    : '自动模式会先查看每个样本的有效序列量，再选择尽量保留样本的共同深度；第一次分析推荐使用它。';
  $('samplingDepthHelp').className = `field-help ${samplingIsValid() ? '' : 'warn'}`;
  updateReadiness();
}

function updateReadiness(requestPlan = true) {
  updateOptionDependencies();
  const dataReady = Boolean(state.scan?.data_type && state.inputPath);
  const metadataProvided = Boolean(metadataPath());
  const metadata = state.preflight?.metadata || {};
  const metadataReady = !metadataProvided || Boolean(metadata.usable ?? state.metadataValid);
  const classifierReady = isChecked('skipTaxonomy') || Boolean(state.classifier || readValue('classifierPath'));
  const diversityWillSkip = diversityWillBeSkipped();
  const depthReady = diversityWillSkip || samplingIsValid();
  const runtimeReady = Boolean(state.environmentReady && state.qiimeEnv);
  const ancomWillSkip = isChecked('skipAncom') || isChecked('skipTaxonomy') || !metadata.usable || !metadata.group_ready;
  const checks = {
    data: { ok: dataReady, blocking: true, level: dataReady ? 'good' : 'warn', text: dataReady ? `${dataTypeLabel(state.scan.data_type)} · ${state.scan.fastq_count || 0} 个 FASTQ 引用` : '先选择并扫描 manifest / FASTQ' },
    metadata: { ok: metadataReady, blocking: false, level: !metadataProvided ? 'info' : metadataReady ? 'good' : 'warn', text: !metadataProvided ? '未提供；分组相关步骤会自动跳过' : metadataReady ? (metadata.group_ready ? `已校验 · ${metadata.group_column} 可用于分组` : '已校验，但没有两个完整分组值') : '文件未通过校验；依赖它的步骤会自动跳过' },
    runtime: { ok: runtimeReady, blocking: true, level: runtimeReady ? 'good' : 'warn', text: runtimeReady ? `使用 Conda 环境 ${state.qiimeEnv}` : '等待可用的 QIIME2 Conda 环境' },
    classifier: { ok: classifierReady, blocking: true, level: classifierReady ? 'good' : 'warn', text: classifierReady ? (isChecked('skipTaxonomy') ? '已跳过物种分类' : '分类器已准备好') : '请选择分类器，或勾选跳过物种分类' },
    sampling: { ok: depthReady, blocking: !diversityWillSkip, level: diversityWillSkip ? 'info' : depthReady ? 'good' : 'warn', text: diversityWillSkip ? '本次跳过多样性，不需要采样深度' : depthReady ? (selectedSamplingMode() === 'auto' ? '自动计算，适合首次分析' : `固定深度 ${readValue('samplingDepth')}`) : '自定义采样深度必须是正整数' },
    analysis: {
      ok: !state.preflight || Boolean(state.preflight.can_run),
      blocking: Boolean(state.preflight && !state.preflight.can_run),
      level: state.preflight && !state.preflight.can_run ? 'warn' : ancomWillSkip || diversityWillSkip ? 'info' : 'good',
      text: state.preflight?.blockers?.[0]?.message || (ancomWillSkip && diversityWillSkip ? '多样性与差异分析会按提示跳过' : ancomWillSkip ? 'ANCOM 会按提示跳过' : diversityWillSkip ? '多样性会按提示跳过' : '多样性和差异分析均已具备前置条件'),
    },
  };
  Object.entries(checks).forEach(([key, check]) => {
    const item = document.querySelector(`[data-check="${key}"]`);
    if (!item) return;
    item.className = `readiness-item ${check.level}`;
    const dot = item.querySelector('.check-dot');
    const copy = item.querySelector('.check-copy');
    if (dot) dot.textContent = check.level === 'good' ? '✓' : check.level === 'info' ? 'i' : '!';
    if (copy) copy.textContent = check.text;
  });
  const ready = Object.values(checks).every((check) => !check.blocking || check.ok);
  const hasNotes = Object.values(checks).some((check) => check.level === 'info' || check.level === 'warn');
  state.canRun = ready;
  $('runButton').disabled = !ready || state.busy;
  $('runHint').textContent = ready ? (hasNotes ? '可以运行；页面会按提示自动跳过不具备条件的步骤。' : '所有必要信息已准备好，点击一次即可启动完整分析。') : '还差一步：按照上面的提示补齐必要信息。';
  $('readyBadge').textContent = ready ? (hasNotes ? 'READY · WITH NOTES' : 'READY') : 'NEEDS SETUP';
  $('readyBadge').className = `ready-badge ${ready ? (hasNotes ? 'info' : 'good') : 'warn'}`;
  if (requestPlan) schedulePreflight();
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
  if (state.metadata && !state.metadataValid) await validateMetadata(state.metadata);
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
  bind('inputPath', 'input', () => { state.inputPath = readValue('inputPath'); state.scan = null; state.preflight = null; updateReadiness(); });
  bind('fastqPicker', 'change', (event) => uploadFiles(event.target, 'fastq'));
  bind('manifestPicker', 'change', (event) => uploadFiles(event.target, 'manifest'));
  bind('classifierPicker', 'change', (event) => uploadFiles(event.target, 'classifier'));
  bind('metadataPicker', 'change', (event) => uploadFiles(event.target, 'metadata'));
  bind('manifestButton', 'click', generateManifest);
  bind('manifestCreateButton', 'click', createManifestEditor);
  bind('metadataButton', 'click', generateMetadata);
  bind('manifestAddRowButton', 'click', addManifestRow);
  bind('manifestPreviewButton', 'click', () => state.manifestEditor?.isNew ? createManifestEditor() : loadManifestPreview(state.manifestEditor?.path || state.inputPath, false));
  bind('manifestSaveButton', 'click', saveManifestEditor);
  bind('manifestTypeSelect', 'change', (event) => changeManifestType(event.target.value));
  bind('metadataAddColumnButton', 'click', addMetadataColumn);
  bind('metadataPreviewButton', 'click', () => loadMetadataPreview(state.metadata || readValue('metadataPath'), false));
  bind('metadataValidateButton', 'click', () => validateMetadata(state.metadata || readValue('metadataPath')));
  bind('metadataSaveButton', 'click', saveMetadataEditor);
  bind('addGroupButton', 'click', addMetadataGroup);
  bind('groupNameInput', 'keydown', (event) => { if (event.key === 'Enter') addMetadataGroup(); });
  bind('groupSampleSearch', 'input', renderGroupSamplePicker);
  bind('selectAllSamples', 'click', () => { if (!state.metadataEditor) return; state.metadataEditor.selectedSamples = new Set(state.metadataEditor.rows.map((_, index) => index)); renderGroupSamplePicker(); });
  bind('selectUnassignedSamples', 'click', () => { if (!state.metadataEditor) return; const column = metadataGroupColumn(); state.metadataEditor.selectedSamples = new Set(state.metadataEditor.rows.map((row, index) => row[column] ? null : index).filter((index) => index !== null)); renderGroupSamplePicker(); });
  bind('clearSampleSelection', 'click', () => { if (!state.metadataEditor) return; state.metadataEditor.selectedSamples.clear(); renderGroupSamplePicker(); });
  bind('confirmCustomColumn', 'click', () => { if (addMetadataColumn(readValue('customColumnName'), readValue('customColumnType', 'categorical'))) { $('customColumnName').value = ''; $('customColumnForm').hidden = true; } });
  bind('runButton', 'click', runAnalysis);
  bind('refreshEnvironments', 'click', loadEnvironments);
  bind('environmentSelect', 'change', (event) => {
    state.qiimeEnv = event.target.value;
    state.selectedEnvironment = state.environments.find((environment) => environment.name === state.qiimeEnv) || null;
    state.environmentReady = Boolean(state.selectedEnvironment?.qiime_available);
    state.preflight = null;
    $('environmentStatus').textContent = state.qiimeEnv ? `✓ 已选择 ${state.qiimeEnv}` : '请选择包含 QIIME2 的环境';
    $('environmentStatus').className = `environment-status ${state.qiimeEnv ? 'good' : 'warn'}`;
    updateFigaroStatus(state.selectedEnvironment);
    updateReadiness();
    schedulePreflight();
  });
  bind('installVersion', 'change', updateInstallSummary);
  bind('installDistribution', 'change', updateInstallSummary);
  bind('installEnvironmentName', 'input', updateInstallSummary);
  bind('installButton', 'click', installQiime);
  bind('figaroInstallButton', 'click', installFigaro);
  bind('metadataPath', 'input', () => { state.metadata = readValue('metadataPath'); state.metadataValid = false; state.preflight = null; $('metadataName').textContent = state.metadata ? '等待校验服务器路径' : '未选择 metadata'; updateMetadataCapability(); updateReadiness(); });
  bind('metadataPath', 'blur', async () => { const path = readValue('metadataPath'); if (!path) return; if (await loadMetadataPreview(path, true)) await validateMetadata(path, true); else { $('metadataValidation').textContent = '无法打开这个 metadata 路径，请检查文件是否存在。'; $('metadataValidation').className = 'validation-note warn'; } });
  bind('classifierPath', 'input', () => { state.classifier = readValue('classifierPath'); state.preflight = null; $('classifierName').textContent = state.classifier ? '使用服务器路径' : '未选择分类器'; updateReadiness(); });
  bind('outputPath', 'input', () => { state.preflight = null; updateReadiness(); });
  ['primerF', 'primerR', 'truncLenF', 'truncLenR', 'minQuality', 'minFrequency'].forEach((id) => bind(id, 'input', updateReadiness));
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
  $('groupSamplePicker')?.addEventListener('change', (event) => {
    const checkbox = event.target.closest('[data-group-sample]');
    if (!checkbox || !state.metadataEditor) return;
    const index = Number(checkbox.dataset.groupSample);
    if (checkbox.checked) state.metadataEditor.selectedSamples.add(index); else state.metadataEditor.selectedSamples.delete(index);
  });
  $('metadataColumnTemplates')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-column-template]');
    if (!button) return;
    const template = METADATA_COLUMN_TEMPLATES.find((item) => item.name === button.dataset.columnTemplate);
    if (template && addMetadataColumn(template.name, template.type)) toast(`${template.label}字段已添加。`, true);
  });
  $('metadataEditorTable')?.addEventListener('input', (event) => {
    const target = event.target.closest('[data-meta-row][data-meta-column]');
    if (!target || !state.metadataEditor) return;
    state.metadataEditor.rows[Number(target.dataset.metaRow)][target.dataset.metaColumn] = target.value;
    markMetadataChanged();
  });
  $('metadataEditorTable')?.addEventListener('change', (event) => {
    const typeSelect = event.target.closest('[data-column-type]');
    if (typeSelect && state.metadataEditor) { state.metadataEditor.types[Number(typeSelect.dataset.columnType)] = typeSelect.value; markMetadataChanged(); return; }
    const target = event.target.closest('[data-meta-row][data-meta-column]');
    if (!target || !state.metadataEditor) return;
    state.metadataEditor.rows[Number(target.dataset.metaRow)][target.dataset.metaColumn] = target.value;
    if (target.dataset.metaColumn === metadataGroupColumn()) renderMetadataGroups();
    markMetadataChanged();
  });
  $('metadataEditorTable')?.addEventListener('click', (event) => {
    const column = event.target.closest('[data-remove-column]')?.dataset.removeColumn;
    if (column) removeMetadataColumn(column);
    const row = event.target.closest('[data-remove-metadata-row]')?.dataset.removeMetadataRow;
    if (row !== undefined && window.confirm('确定删除这个样本吗？')) { state.metadataEditor.rows.splice(Number(row), 1); renderMetadataGroups(); renderMetadataTable(); markMetadataChanged(); }
  });
  $('manifestEditorTable')?.addEventListener('input', (event) => {
    const target = event.target.closest('[data-manifest-row][data-manifest-column]');
    if (target && state.manifestEditor) { state.manifestEditor.rows[Number(target.dataset.manifestRow)][target.dataset.manifestColumn] = target.value; state.manifestEditor.path_status = []; target.classList.toggle('is-empty', !target.value.trim()); }
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
  ['samplingDepth', 'noTrim', 'noFilter', 'noFigaro'].forEach((id) => bind(id, 'input', updateReadiness));
  bind('skipTaxonomy', 'change', () => { updateOptionDependencies(); updateReadiness(); schedulePreflight(); });
  bind('skipDiversity', 'change', () => { updateSamplingMode(); schedulePreflight(); });
  bind('skipAncom', 'change', (event) => { state.ancomChoice = event.target.checked; updateReadiness(); schedulePreflight(); });
  loadClassifiers();
  checkHealth();
});
