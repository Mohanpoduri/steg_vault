// Helper to show Bootstrap Toast
function showToast(message, isError = false) {
    const $toast = $('#globalToast');
    const $toastBody = $('#toastMessage');
    
    $toast.removeClass('bg-success bg-danger text-white');
    if (isError) $toast.addClass('bg-danger text-white');
    else $toast.addClass('bg-success text-white');
    
    $toastBody.text(message);
    const bsToast = new bootstrap.Toast($toast[0]);
    bsToast.show();
}

// Generate unique ID for chunked uploads
function uuidv4() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

// Web Audio API Global Contexts
let audioCtxEnc = null, audioCtxDec = null;

function setupAudioVisualizer(file, isEncode = true) {
    const player = document.getElementById(isEncode ? 'encodeAudioPlayer' : 'decodeAudioPlayer');
    const canvas = document.getElementById(isEncode ? 'encodeWaveform' : 'decodeWaveform');
    const canvasCtx = canvas.getContext('2d');
    const timeDisplay = document.getElementById(isEncode ? 'encodeAudioTime' : 'decodeAudioTime');
    
    // Reveal UI
    $(isEncode ? '#encodeAudioVisualizer' : '#decodeAudioVisualizer').removeClass('d-none');
    $(player).removeClass('d-none');
    player.src = URL.createObjectURL(file);

    // Initialize Audio Context lazily on play
    player.onplay = () => {
        let actx = isEncode ? audioCtxEnc : audioCtxDec;
        if (!actx) {
            actx = new (window.AudioContext || window.webkitAudioContext)();
            const src = actx.createMediaElementSource(player);
            const analyser = actx.createAnalyser();
            analyser.fftSize = 256;
            src.connect(analyser);
            analyser.connect(actx.destination);
            
            if (isEncode) audioCtxEnc = actx;
            else audioCtxDec = actx;

            const bufferLength = analyser.frequencyBinCount;
            const dataArray = new Uint8Array(bufferLength);

            function draw() {
                requestAnimationFrame(draw);
                if (player.paused) return; // Stop drawing when paused

                analyser.getByteFrequencyData(dataArray);
                canvasCtx.fillStyle = '#0b0f19';
                canvasCtx.fillRect(0, 0, canvas.width, canvas.height);

                const barWidth = (canvas.width / bufferLength) * 2.5;
                let barHeight;
                let x = 0;

                for(let i = 0; i < bufferLength; i++) {
                    barHeight = dataArray[i];
                    canvasCtx.fillStyle = '#00FF9D'; // Neon Mint Green
                    canvasCtx.fillRect(x, canvas.height - barHeight/2, barWidth, barHeight/2);
                    x += barWidth + 1;
                }
            }
            draw();
        }
        if (actx.state === 'suspended') actx.resume();
    };

    player.ontimeupdate = () => {
        const mins = Math.floor(player.currentTime / 60);
        const secs = Math.floor(player.currentTime % 60);
        timeDisplay.innerText = `${mins}:${secs.toString().padStart(2, '0')}`;
    };
}

$(document).ready(function() {
    
    let maxCharCapacity = 0;
    let currentMode = 'image'; // image, audio, video
    const CHUNK_SIZE = 2 * 1024 * 1024; // 2MB Chunk Limit
    const MAX_STANDARD_SIZE = 5 * 1024 * 1024; // 5MB Threshold for chunking

    // Mode Toggle Logic
    $('.media-toggle').on('change', function() {
        currentMode = $(this).val();
        
        $('#encodeMediaType').val(currentMode);
        $('#decodeMediaType').val(currentMode);
        $('#encodeForm')[0].reset();
        $('#decodeForm')[0].reset();
        
        const $encInput = $('#encodeFile');
        const $decInput = $('#decodeFile');
        const $encText = $('#encodePromptText');
        const $decText = $('#decodePromptText');
        const $encIcon = $('#encodePromptIcon');
        const $decIcon = $('#decodePromptIcon');

        resetPreviews();
        
        if (currentMode === 'image') {
            $encInput.attr('accept', 'image/png, image/jpeg');
            $decInput.attr('accept', 'image/png');
            $encText.text('Drop Image (PNG/JPG)');
            $decText.text('Drop Encoded Image to Audit (.png)');
            $encIcon.attr('class', 'bi bi-image-fill');
            $decIcon.attr('class', 'bi bi-file-image-fill');
            $('#capacityLabel').text('0 / 0 Chars');
        } else if (currentMode === 'audio') {
            $encInput.attr('accept', 'audio/wav');
            $decInput.attr('accept', 'audio/wav');
            $encText.text('Drop Audio (.wav)');
            $decText.text('Drop Encoded Audio (.wav)');
            $encIcon.attr('class', 'bi bi-soundwave');
            $decIcon.attr('class', 'bi bi-file-earmark-music-fill');
            $('#capacityLabel').text('Adaptive Length (WAV LSB)');
        } else if (currentMode === 'video') {
            $encInput.attr('accept', 'video/mp4, video/avi, video/x-msvideo');
            $decInput.attr('accept', 'video/mp4, video/avi, video/x-msvideo');
            $encText.text('Drop Video (.mp4, .avi)');
            $decText.text('Drop Encoded Video (.mp4, .avi)');
            $encIcon.attr('class', 'bi bi-camera-reels-fill');
            $decIcon.attr('class', 'bi bi-file-earmark-play-fill');
            $('#capacityLabel').text('Unlimited (EOF Append)');
        }
        updateCapacityMeter(); 
    });

    function resetPreviews() {
        $('#encodeDropPrompt').removeClass('d-none');
        $('#encodePreviewContainer').addClass('d-none');
        $('#encodeImagePreview').addClass('d-none').attr('src', '');
        $('#encodeIconPreview').addClass('d-none');

        $('#decodeDropPrompt').removeClass('d-none');
        $('#decodePreviewContainer').addClass('d-none');
        $('#decodeImagePreview').addClass('d-none').attr('src', '');
        $('#decodeIconPreview').addClass('d-none');
        
        $('#encodeAudioVisualizer').addClass('d-none');
        $('#decodeAudioVisualizer').addClass('d-none');
        if (audioCtxEnc) { document.getElementById('encodeAudioPlayer').pause(); }
        if (audioCtxDec) { document.getElementById('decodeAudioPlayer').pause(); }

        maxCharCapacity = 0;
    }

    // Generic Drop Zone Styling
    function setupDropZone(inputId, targetZone) {
        $(inputId).on('dragenter dragover', function(e) { e.preventDefault(); $(targetZone).addClass('bg-secondary'); })
                  .on('dragleave drop', function(e) { $(targetZone).removeClass('bg-secondary'); });
    }
    setupDropZone('#encodeFile', '#encodeDropZone');
    setupDropZone('#decodeFile', '#decodeDropZone');
    setupDropZone('#forensicImage', '#forensicImage');

    $('#encodeFile').on('change', function(e) { handleFileSelection(e, 'encode'); });
    $('#decodeFile').on('change', function(e) { handleFileSelection(e, 'decode'); });

    function handleFileSelection(e, type) {
        const file = e.target.files[0];
        if (!file) return;

        const filenameId = `#${type}Filename`;
        const promptId = `#${type}DropPrompt`;
        const previewContainerId = `#${type}PreviewContainer`;
        const imagePreviewId = `#${type}ImagePreview`;
        const iconPreviewId = `#${type}IconPreview`;

        $(filenameId).text(file.name);
        $(promptId).addClass('d-none');
        $(previewContainerId).removeClass('d-none');

        if (currentMode === 'image') {
            const reader = new FileReader();
            reader.onload = function(event) {
                $(imagePreviewId).attr('src', event.target.result).removeClass('d-none');
                $(iconPreviewId).addClass('d-none');
                if (type === 'encode') {
                    const img = new Image();
                    img.onload = function() {
                        maxCharCapacity = Math.max(0, Math.floor((img.naturalWidth * img.naturalHeight * 3) / 8) - 71);
                        updateCapacityMeter();
                    };
                    img.src = event.target.result;
                }
            };
            reader.readAsDataURL(file);
        } else {
            $(imagePreviewId).addClass('d-none');
            $(iconPreviewId).removeClass('d-none').html(currentMode === 'audio' ? '<i class="bi bi-file-music text-cyan"></i>' : '<i class="bi bi-file-play text-cyan"></i>');
            if (currentMode === 'audio') {
                setupAudioVisualizer(file, type === 'encode');
            }
        }
    }

    $('#secretText').on('input', updateCapacityMeter);

    function updateCapacityMeter() {
        const currentLen = $('#secretText').val().length;
        if (currentMode !== 'image') {
            $('#capacityMeter').css('width', '100%').addClass('bg-success').removeClass('bg-danger bg-cyan');
            $('#encodeBtn').prop('disabled', false);
            return;
        }
        if (maxCharCapacity === 0) {
            $('#capacityLabel').text('0 / 0 Chars');
            $('#capacityMeter').css('width', '0%');
            return;
        }
        $('#capacityLabel').text(`${currentLen} / ${maxCharCapacity} Chars`);
        let pct = (currentLen / maxCharCapacity) * 100;
        if (pct > 100) pct = 100;
        $('#capacityMeter').css('width', `${pct}%`);
        
        if (currentLen > maxCharCapacity) {
            $('#capacityMeter').removeClass('bg-cyan bg-success').addClass('bg-danger');
            $('#encodeBtn').prop('disabled', true);
            $('#capacityLabel').addClass('text-danger').removeClass('text-cyan');
        } else {
            $('#capacityMeter').removeClass('bg-danger bg-cyan').addClass('bg-cyan');
            $('#encodeBtn').prop('disabled', false);
            $('#capacityLabel').removeClass('text-danger').addClass('text-cyan');
        }
    }

    // --- ENCODE AJAX (Supports Chuncked Upload) ---
    $('#encodeForm').on('submit', async function(e) {
        e.preventDefault();
        const file = $('#encodeFile')[0].files[0];
        if (!file) { showToast("Provide a payload medium.", true); return; }

        const $btn = $('#encodeBtn');
        const $spinner = $('#encodeSpinner');
        const $text = $btn.find('.btn-text');
        
        $btn.prop('disabled', true);
        $text.html('0% PROCESS');
        $spinner.removeClass('d-none');
        
        // Large File Chunking Handler
        if (file.size > MAX_STANDARD_SIZE) {
            showToast("Large file detected. Initiating chunked protocol...", false);
            const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
            const fileId = uuidv4();
            
            for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex++) {
                const start = chunkIndex * CHUNK_SIZE;
                const end = Math.min(start + CHUNK_SIZE, file.size);
                const chunk = file.slice(start, end);

                let chunkData = new FormData();
                chunkData.append('chunk', chunk);
                chunkData.append('file_id', fileId);
                chunkData.append('chunk_index', chunkIndex);
                chunkData.append('total_chunks', totalChunks);
                chunkData.append('media_type', currentMode);
                chunkData.append('secret_text', $('#secretText').val());
                chunkData.append('aes_key', $('#encodeAesKey').val());
                chunkData.append('original_filename', file.name);

                try {
                    const res = await $.ajax({
                        url: '/api/upload_chunk', type: 'POST', data: chunkData,
                        processData: false, contentType: false
                    });
                    
                    const percent = Math.floor(((chunkIndex + 1) / totalChunks) * 100);
                    $text.html(`${percent}% COMPLETED`);
                    $('#capacityMeter').css('width', `${percent}%`).removeClass('bg-danger bg-cyan').addClass('bg-warning');

                    if (chunkIndex === totalChunks - 1) {
                        if (res.success && res.download_url) {
                            showToast(res.message);
                            triggerDownload(res.download_url);
                        }
                    }
                } catch (xhr) {
                    const msg = xhr.responseJSON ? xhr.responseJSON.error : "Network fault during chunk upload.";
                    showToast(msg, true);
                    break;
                }
            }
            cleanupEncode();
        } else {
            // Standard form
            const formData = new FormData(this);
            $.ajax({
                url: '/api/encode', type: 'POST', data: formData,
                processData: false, contentType: false,
                success: function(res) {
                    if (res.success) {
                        showToast(res.message);
                        triggerDownload(res.download_url);
                        cleanupEncode();
                    } else showToast(res.error, true);
                },
                error: function(xhr) { showToast(xhr.responseJSON?.error || "Error", true); cleanupEncode(); },
                complete: function() { cleanupEncode(); }
            });
        }
    });

    function triggerDownload(url) {
        const a = document.createElement('a');
        a.href = url;
        document.body.appendChild(a);
        a.click();
        a.remove();
    }

    function cleanupEncode() {
        const $btn = $('#encodeBtn');
        $btn.prop('disabled', false);
        $btn.find('.btn-text').html('EMBED SECURELY');
        $('#encodeSpinner').addClass('d-none');
        $('#encodeForm')[0].reset();
        resetPreviews();
        updateCapacityMeter();
    }

    // --- DECODE AJAX ---
    $('#decodeForm').on('submit', function(e) {
        e.preventDefault();
        const file = $('#decodeFile')[0].files[0];
        if (!file) { showToast("Provide secure media for decoding.", true); return; }
        
        const formData = new FormData(this);
        const $btn = $('#decodeBtn');
        $btn.prop('disabled', true);
        $btn.find('.btn-text').addClass('opacity-0');
        $('#decodeSpinner').removeClass('d-none');
        
        $.ajax({
            url: '/api/decode', type: 'POST', data: formData,
            processData: false, contentType: false,
            success: function(res) {
                if (res.success) {
                    showToast("Access Granted. Payload decrypted strictly.");
                    $('#extractedText').val(res.message);
                    $('#extractedText').animate({ backgroundColor: 'rgba(25, 135, 84, 0.3)' }, 300)
                                       .animate({ backgroundColor: 'rgba(25, 135, 84, 0.1)' }, 1000);
                } else showToast(res.error, true);
            },
            error: function(xhr) { showToast(xhr.responseJSON?.error, true); },
            complete: function() {
                $btn.prop('disabled', false);
                $btn.find('.btn-text').removeClass('opacity-0');
                $('#decodeSpinner').addClass('d-none');
            }
        });
    });

    // --- FORENSICS AJAX ---
    $('#forensicsForm').on('submit', function(e) {
        e.preventDefault();
        const file = $('#forensicFile')[0].files[0];
        if (!file) { showToast("Media required for pipeline.", true); return; }
        
        const formData = new FormData(this);
        const $btn = $('#analyzeBtn');
        $btn.prop('disabled', true);
        $btn.find('.btn-text').addClass('opacity-0');
        $('#analyzeSpinner').removeClass('d-none');
        
        // Preview Original Image manually based on selection
        if (file.type.startsWith('image/')) {
            const reader = new FileReader();
            reader.onload = function(evt) {
                $('#forensicOriginalPreview').attr('src', evt.target.result).removeClass('d-none');
                $('#forensicOriginalIcon').addClass('d-none');
            };
            reader.readAsDataURL(file);
        } else {
            $('#forensicOriginalPreview').addClass('d-none');
            $('#forensicOriginalIcon').removeClass('d-none').html(
                file.type.startsWith('audio/') 
                ? '<i class="bi bi-file-music" style="font-size: 5rem;"></i><p>Audio Track</p>' 
                : '<i class="bi bi-file-play" style="font-size: 5rem;"></i><p>Video Core</p>'
            );
        }

        $.ajax({
            url: '/api/analyze', type: 'POST', data: formData,
            processData: false, contentType: false,
            success: function(res) {
                if (res.success) {
                    $('#forensicResultPreview').attr('src', res.download_url + '?t=' + new Date().getTime());
                    $('#forensicDownloadBtn').attr('href', res.download_url);
                    $('#forensicResultsContainer').removeClass('d-none');
                    showToast("Forensic array applied successfully.");
                } else showToast(res.error, true);
            },
            error: function(xhr) { showToast(xhr.responseJSON?.error || "CV Failure.", true); },
            complete: function() {
                $btn.prop('disabled', false);
                $btn.find('.btn-text').removeClass('opacity-0');
                $('#analyzeSpinner').addClass('d-none');
            }
        });
    });
});
