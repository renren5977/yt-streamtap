(function() {
    window.__segmentBuffer__ = [];
    let __seq__ = 0;
    window.__captureEnable__ = true;
    window.__clearSegmentBuffer__ = false;
    window.__removeSourceBuffer__ = false;

    const sbTrackMap = new WeakMap();
    const sbList = [];

    // --- MediaSourceフック ---
    const origAdd = MediaSource.prototype.addSourceBuffer;
    MediaSource.prototype.addSourceBuffer = function(mimeType) {
        const sb = origAdd.apply(this, arguments);
        sbTrackMap.set(sb, mimeType);
        return sb;
    };

    // --- SourceBufferフック ---
    const origAppend = SourceBuffer.prototype.appendBuffer;

    SourceBuffer.prototype.appendBuffer = function(data) {
        const track = sbTrackMap.get(this);
        sbList.push(this);

        // データ変換
        let bytes;
        if (data instanceof ArrayBuffer) {
            bytes = new Uint8Array(data);
        } else if (ArrayBuffer.isView(data)) {
            bytes = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
        } else {
            return origAppend.apply(this, arguments);
        }

        if (window.__clearBufferRequested__) {
            window.__clearBufferRequested__ = false;
            window.__segmentBuffer__ = [];
        }

        // キャプチャ
        if (window.__captureEnable__) {
            const CHUNK = 0x8000;
            let s = '';
            for (let i = 0; i < bytes.length; i += CHUNK) {
                s += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
            }
            window.__segmentBuffer__.push({
                track: track,
                data: btoa(s),   // ★ Uint8Array → base64 string
                seq: __seq__++
            });
        }

        return origAppend.apply(this, arguments);
    };

    window.__getBufferState__ = function() {
        const result = {};
        sbList.forEach((sb) => {
            const ranges = [];
            for (let i = 0; i < sb.buffered.length; i++) {
                ranges.push([sb.buffered.start(i), sb.buffered.end(i)]);
            }
            result[track] = ranges;
        });
        return result;
    };

})();
