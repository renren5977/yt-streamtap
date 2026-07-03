(function() {
    "use strict";

    window.__segmentBuffer__ = [];
    let __seq__ = 0;
    window.__captureEnable__ = true;
    window.__clearBufferRequested__ = false;

    // ---- 追跡マップ ----
    // SourceBuffer → mimeType
    const sbToMime = new WeakMap();
    // MediaSource → SourceBuffer のリスト (通常は video/audio の2つ)
    const urlToMS = new Map();       // blob:URL → MediaSource
    const msToSBs = new Map();       // MediaSource → SourceBuffer[]
    const mainVideoURLs = new Set(); // メイン動画に割り当てられた blob:URL

    // ---- URL.createObjectURL フック ----
    const _origCreate = URL.createObjectURL.bind(URL);
    URL.createObjectURL = function(obj) {
        const url = _origCreate(obj);
        if (obj instanceof MediaSource) {
            urlToMS.set(url, obj);
        }
        return url;
    };
    const _origRevoke = URL.revokeObjectURL.bind(URL);
    URL.revokeObjectURL = function(url) {
        urlToMS.delete(url);
        mainVideoURLs.delete(url);
        return _origRevoke(url);
    };

    // ---- HTMLVideoElement.src フック ----
    // メイン動画要素に blob:URL がセットされたら記録
    const _srcDesc = Object.getOwnPropertyDescriptor(
        HTMLMediaElement.prototype, 'src'
    );
    if (_srcDesc && _srcDesc.set) {
        Object.defineProperty(HTMLMediaElement.prototype, 'src', {
            set: function(val) {
                _srcDesc.set.call(this, val);
                if (typeof val === 'string' && val.startsWith('blob:') &&
                    (this.classList.contains('video-stream') ||
                     this.classList.contains('html5-main-video'))) {
                    mainVideoURLs.add(val);
                }
            },
            get: function() { return _srcDesc.get.call(this); },
        });
    }

    // ---- メイン動画のMediaSourceに属するSourceBufferか判定 ----
    function _isMainVideoSourceBuffer(sb) {
        // sb → MediaSource を逆引き
        for (const [url, ms] of urlToMS) {
            if (!mainVideoURLs.has(url)) continue; // メイン動画以外のURL
            const sbs = msToSBs.get(ms);
            if (sbs && sbs.indexOf(sb) !== -1) {
                return true;
            }
        }
        return false;
    }

    // ---- MediaSource.addSourceBuffer フック ----
    const _origAddSB = MediaSource.prototype.addSourceBuffer;
    MediaSource.prototype.addSourceBuffer = function(mimeType) {
        const sb = _origAddSB.apply(this, arguments);
        sbToMime.set(sb, mimeType);

        let list = msToSBs.get(this);
        if (!list) { list = []; msToSBs.set(this, list); }
        list.push(sb);
        return sb;
    };

    // ---- SourceBuffer.appendBuffer フック ----
    const _origAppend = SourceBuffer.prototype.appendBuffer;
    SourceBuffer.prototype.appendBuffer = function(data) {
        // ★ メイン動画以外の SourceBuffer からのデータはキャプチャしない ★
        if (mainVideoURLs.size > 0 && !_isMainVideoSourceBuffer(this)) {
            return _origAppend.apply(this, arguments);
        }

        // データ変換
        let bytes;
        if (data instanceof ArrayBuffer) {
            bytes = new Uint8Array(data);
        } else if (ArrayBuffer.isView(data)) {
            bytes = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
        } else {
            return _origAppend.apply(this, arguments);
        }

        if (window.__clearBufferRequested__) {
            window.__clearBufferRequested__ = false;
            window.__segmentBuffer__ = [];
        }

        if (window.__captureEnable__) {
            const CHUNK = 0x8000;
            let s = '';
            for (let i = 0; i < bytes.length; i += CHUNK) {
                s += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
            }
            // その時点の再生時間を取得
            let videoTime = -1;
            try {
                const v = document.querySelector('video.video-stream, video.html5-main-video');
                if (v) videoTime = v.currentTime;
            } catch(e) {}

            window.__segmentBuffer__.push({
                track: sbToMime.get(this) || '',
                data: btoa(s),
                seq: __seq__++,
                videoTime: videoTime,
            });
        }
        return _origAppend.apply(this, arguments);
    };

    window.__popSegment__ = function() {
        if (!window.__segmentBuffer__ || window.__segmentBuffer__.length === 0) return null;
        return window.__segmentBuffer__.shift();
    };
})();