const state = { scan: null, metadata: null, command: '' };
const $ = (id) => document.getElementById(id);

function toast(message, success = false) {
  $('toastText').textContent = message;
  $('toast').classList.toggle('success', success);
  $('toast').classList.add('show');
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => $('toast').classList.remove('show'), 4200);
}

async function api(url, options = {}) {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...options });
  const payload = await response.json();
  if (!response.ok && !payload.validation) throw new Error(payload.error || '请求失败');
  return payload;
}

function setBusy(button, busy, busyText = '处理中…') {
  if (!button) return;
  if (busy) { button.dataset.label = button.textContent; button.textContent = busyText; button.disabled = true; }
  else { button.textContent = button.dataset.label || button.textContent; button.disabled = false; }
}

function markStep(name, status, label) {
  const step = document.querySelector(`[data-step="${name}"]`);
  if (!step) return;
  step.classList.toggle('done', status === 'done');
  step.classList.toggle('active', status === 'active');
  const stateEl = step.querySelector('.step-state');
  if (stateEl) stateEl.textContent = label || (status === 'done' ? '已完成' : '待处理');
}

async function checkHealth() {
  try {
    const data = await api('/api/health');
    const hasQiime = data.tools.qiime;
    $('healthDot').classList.add('good'); $('healthText').textContent = hasQiime ? 'QIIME2 环境已就绪' : '前置工具可用 · QIIME2 未安装';
    $('qiimeValue').textContent = hasQiime ? 'READY' : 'PREP MODE';
    $('qiimeDesc').textContent = hasQiime ? '可以在完成校验后启动分析。' : '可以扫描与准备输入，分析需在 QIIME2 环境执行。';
  } catch (error) { $('healthText').textContent = '本地服务连接失败'; $('qiimeValue').textContent = 'OFFLINE'; toast(error.message); }
}

function showScan(scan) {
  state.scan = scan;
  const result = $('scanResult'); result.classList.remove('empty');
  result.innerHTML = `<span class="scan-icon">${scan.data_type ? '✓' : '!'}</span><span><strong>${scan.data_type || '无法识别'}</strong><br>${scan.exists ? `${scan.fastq_count || 0} 个 FASTQ 文件 · ${scan.paired_end ? '双端' : '单端/清单'}` : '路径不存在，请检查输入。'}</span>`;
  $('dataTypeValue').textContent = scan.data_type || 'UNKNOWN';
  $('dataTypeDesc').textContent = scan.data_type ? (scan.paired_end ? '检测为双端数据。' : '检测为单端或清单数据。') : '请检查文件名、扩展名或准备 manifest.tsv。';
  $('sampleCountValue').textContent = scan.fastq_count ?? '—';
  $('sampleCountDesc').textContent = scan.fastq_count ? '目录中的 FASTQ 文件数量。' : '如果输入是 manifest，请继续准备 metadata。';
  $('manifestButton').disabled = !(scan.kind === 'directory' && scan.data_type);
  $('metadataButton').disabled = !(scan.data_type && scan.data_type.startsWith('manifest'));
  markStep('scan', 'done', '已完成'); markStep('manifest', scan.kind === 'directory' ? 'active' : 'done');
  if (scan.warnings?.length) toast(scan.warnings[0]); else toast('扫描完成，可以继续下一步。', true);
}

async function scan() {
  const path = $('inputPath').value.trim(); if (!path) { toast('请先输入文件或目录路径。'); return; }
  setBusy($('scanButton'), true, '…');
  try { const data = await api(`/api/scan?path=${encodeURIComponent(path)}`); showScan(data.scan); } catch (error) { toast(error.message); } finally { setBusy($('scanButton'), false); }
}

async function generateManifest() {
  if (!state.scan) return;
  setBusy($('manifestButton'), true, '生成…');
  try { const data = await api('/api/manifest', { method: 'POST', body: JSON.stringify({ input_path: state.scan.path, paired_end: state.scan.paired_end }) }); $('inputPath').value = data.path; showScan(data.scan); markStep('manifest', 'done', '已生成'); markStep('metadata', 'active'); $('metadataButton').disabled = false; toast('manifest.tsv 已生成。', true); } catch (error) { toast(error.message); } finally { setBusy($('manifestButton'), false); }
}

async function generateMetadata() {
  setBusy($('metadataButton'), true, '生成…');
  try { const data = await api('/api/metadata', { method: 'POST', body: JSON.stringify({ source_path: $('inputPath').value.trim(), columns: ['group'] }) }); state.metadata = data.path; toast(`metadata 模板已生成（${data.sample_count} 个样本）。`, true); markStep('metadata', 'done', '已生成'); markStep('run', 'active'); await validateMetadata(data.path); } catch (error) { toast(error.message); } finally { setBusy($('metadataButton'), false); }
}

async function validateMetadata(path) {
  try {
    const data = await api('/api/validate-metadata', { method: 'POST', body: JSON.stringify({ path }) });
    if (!data.ok || !data.validation?.valid) throw new Error((data.validation?.errors || ['metadata 校验失败']).join('；'));
    state.metadata = path; markStep('metadata', 'done', '已通过'); $('previewButton').disabled = false; toast('metadata 校验通过。', true);
  } catch (error) { if (error.message) toast(error.message); }
}

async function preview() {
  const data = { input_path: $('inputPath').value.trim(), output_dir: 'qiime2_analysis', metadata: state.metadata || 'metadata.tsv' };
  try { const result = await api('/api/preview', { method: 'POST', body: JSON.stringify(data) }); state.command = result.command; $('commandOutput').textContent = result.command; $('copyButton').disabled = false; $('runButton').disabled = false; $('commandHint').textContent = '命令已生成；确认 metadata 和分类器路径后再运行。'; markStep('run', 'done', '可执行'); toast('命令预览已生成。', true); } catch (error) { toast(error.message); }
}

async function copyCommand() { try { await navigator.clipboard.writeText(state.command); toast('命令已复制到剪贴板。', true); } catch { toast('复制失败，请手动选择命令。'); } }

async function runAnalysis() {
  if (!state.scan?.data_type || !state.metadata) { toast('请先完成输入扫描和 metadata 校验。'); return; }
  setBusy($('runButton'), true, '启动中…');
  try {
    const data = await api('/api/run', { method: 'POST', body: JSON.stringify({ input_path: state.scan.path, output_dir: 'qiime2_analysis', data_type: state.scan.data_type, metadata: state.metadata, sampling_depth: 'auto' }) });
    toast(`任务 ${data.job_id} 已启动，正在检查状态。`, true);
    const poll = async () => {
      const current = await api(`/api/jobs/${data.job_id}`);
      if (current.job.status === 'running') { window.setTimeout(poll, 1200); return; }
      toast(current.job.status === 'completed' ? '分析完成，请查看输出目录。' : `任务未完成：${current.job.message || '请查看日志。'}`, current.job.status === 'completed');
      setBusy($('runButton'), false);
    };
    window.setTimeout(poll, 700);
  } catch (error) { toast(error.message); setBusy($('runButton'), false); }
}

document.addEventListener('DOMContentLoaded', () => {
  checkHealth();
  $('scanButton').addEventListener('click', scan); $('inputPath').addEventListener('keydown', (event) => { if (event.key === 'Enter') scan(); });
  $('manifestButton').addEventListener('click', generateManifest); $('metadataButton').addEventListener('click', generateMetadata); $('previewButton').addEventListener('click', preview); $('copyButton').addEventListener('click', copyCommand);
  $('runButton').addEventListener('click', runAnalysis);
});
