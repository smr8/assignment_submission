/**
 * 分片上传：把大文件切成小块逐个 POST，避免单次请求体超过服务器上限，
 * 同时用 XHR 的上传进度事件驱动进度条。
 *
 * 用法：给 <form> 加上以下 data 属性即可
 *   data-upload-url   分片上传接口地址
 *   data-chunk-size   分片大小（字节）
 *   data-max-size     允许的最大文件大小（字节，0 表示不限制）
 *   data-mode         student | admin
 *   data-project      学生端为 project slug，管理端为 project id
 *   data-student-pk   管理端需要，学生 id
 *   data-file-label   可选，文件名显示位置
 */
(function () {
  "use strict";

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(2) + " MB";
    return (bytes / 1024 / 1024 / 1024).toFixed(2) + " GB";
  }

  function newUploadId() {
    return (
      Date.now().toString(36) +
      "-" +
      Math.random().toString(36).slice(2, 10) +
      Math.random().toString(36).slice(2, 6)
    );
  }

  function getCsrf(form) {
    var input = form.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : "";
  }

  function request(url, headers, body) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open("POST", url, true);
      Object.keys(headers).forEach(function (k) {
        xhr.setRequestHeader(k, headers[k]);
      });
      xhr.onload = function () {
        var data = null;
        try {
          data = JSON.parse(xhr.responseText);
        } catch (e) {
          data = { ok: false, error: "服务器返回了无法解析的内容（HTTP " + xhr.status + "）" };
        }
        if (xhr.status >= 200 && xhr.status < 300 && data && data.ok) {
          resolve(data);
        } else {
          reject(new Error((data && data.error) || "HTTP " + xhr.status));
        }
      };
      xhr.onerror = function () {
        reject(new Error("网络错误，请检查连接后重试"));
      };
      xhr.send(body);
    });
  }

  function attach(form) {
    if (!form || form.dataset.chunkReady === "1") return;
    form.dataset.chunkReady = "1";

    // 有 JS 时由脚本接管提交，关掉浏览器原生校验以免拦截 submit 事件
    form.setAttribute("novalidate", "novalidate");

    var uploadUrl = form.dataset.uploadUrl;
    var chunkSize = parseInt(form.dataset.chunkSize || "4194304", 10);
    var maxSize = parseInt(form.dataset.maxSize || "0", 10);
    var mode = form.dataset.mode || "student";
    var project = form.dataset.project || "";
    var studentPk = form.dataset.studentPk || "";

    var fileInput = form.querySelector('input[type="file"][name="file"]');
    var submitBtn = form.querySelector('button[type="submit"], input[type="submit"]');
    var progressWrap = document.getElementById(form.dataset.progressId || "uploadProgress");
    var progressBar = document.getElementById(form.dataset.progressBarId || "uploadProgressBar");
    var progressText = document.getElementById(form.dataset.progressTextId || "uploadProgressText");
    var errorBox = document.getElementById(form.dataset.errorId || "uploadError");

    if (!fileInput || !uploadUrl) return;

    function showError(msg) {
      if (!errorBox) {
        window.alert(msg);
        return;
      }
      errorBox.textContent = msg;
      errorBox.classList.remove("d-none");
    }

    function clearError() {
      if (errorBox) {
        errorBox.textContent = "";
        errorBox.classList.add("d-none");
      }
    }

    function setProgress(percent, text) {
      var p = Math.max(0, Math.min(100, percent));
      if (progressWrap) progressWrap.classList.remove("d-none");
      if (progressBar) {
        progressBar.style.width = p.toFixed(1) + "%";
        progressBar.setAttribute("aria-valuenow", p.toFixed(0));
        if (text) progressBar.textContent = text;
      }
      if (progressText && !text) progressText.textContent = p.toFixed(1) + "%";
    }

    function lock(on) {
      if (submitBtn) submitBtn.disabled = on;
      fileInput.disabled = on;
    }

    function appealValue() {
      var el = form.querySelector('[name="appeal"]');
      return el ? el.value : "";
    }

    function autoRenameValue() {
      var el = form.querySelector('[name="auto_rename"]');
      if (!el) return "1";
      return el.checked ? "1" : "0";
    }

    async function upload(file) {
      var total = Math.ceil(file.size / chunkSize) || 1;
      var uploadId = newUploadId();
      var sentBase = 0;
      clearError();
      lock(true);
      setProgress(0, "0%");

      for (var index = 0; index < total; index++) {
        var start = index * chunkSize;
        var end = Math.min(start + chunkSize, file.size);
        var blob = file.slice(start, end);

        var fd = new FormData();
        fd.append("upload_id", uploadId);
        fd.append("chunk_index", String(index));
        fd.append("total_chunks", String(total));
        fd.append("filename", file.name);
        fd.append("file_size", String(file.size));
        fd.append("project", project);
        fd.append("mode", mode);
        fd.append("student_pk", studentPk);
        fd.append("auto_rename", autoRenameValue());
        fd.append("appeal", appealValue());
        fd.append("chunk", blob, "chunk");

        var attempt = 0;
        // 单个分片失败最多重试 2 次，避免网络抖动整包重来
        for (;;) {
          try {
            var result = await request(uploadUrl, { "X-CSRFToken": getCsrf(form) }, fd);
            sentBase = end;
            setProgress((sentBase / file.size) * 100);
            if (result.done) {
              setProgress(100, "100%");
              if (result.redirect) {
                window.location.href = result.redirect;
                return;
              }
              lock(false);
              return;
            }
            break;
          } catch (err) {
            attempt++;
            if (attempt > 2) {
              lock(false);
              showError("第 " + (index + 1) + " 个分片上传失败：" + err.message);
              return;
            }
          }
        }
      }
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var file = fileInput && fileInput.files && fileInput.files[0];
      if (!file) {
        showError("请先选择要上传的文件。");
        return;
      }
      if (maxSize > 0 && file.size > maxSize) {
        showError("文件大小超过限制（" + formatSize(maxSize) + "）。");
        return;
      }
      upload(file);
    });

    // 选择文件后同步文件名/大小的展示
    var nameEl = form.dataset.fileNameId ? document.getElementById(form.dataset.fileNameId) : null;
    var sizeEl = form.dataset.fileSizeId ? document.getElementById(form.dataset.fileSizeId) : null;
    fileInput.addEventListener("change", function () {
      var file = fileInput.files && fileInput.files[0];
      clearError();
      if (progressWrap) progressWrap.classList.add("d-none");
      if (nameEl) nameEl.textContent = file ? file.name : "";
      if (sizeEl) sizeEl.textContent = file ? "(" + formatSize(file.size) + ")" : "";
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    Array.prototype.forEach.call(
      document.querySelectorAll("form[data-upload-url]"),
      attach
    );
  });

  window.ChunkedUpload = { attach: attach, formatSize: formatSize };
})();
