document.addEventListener('click', async (event) => {
  const button = event.target.closest('button.copy');
  if (!button) return;
  const value = button.dataset.copy;
  const status = document.getElementById('copy-status');
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
    } else {
      throw new Error('clipboard unavailable');
    }
    status.textContent = '已复制';
  } catch (_) {
    const field = document.createElement('textarea');
    field.value = value;
    document.body.appendChild(field);
    field.select();
    const copied = document.execCommand('copy');
    field.remove();
    status.textContent = copied ? '已复制' : '请选中账号或密码手动复制';
  }
});
