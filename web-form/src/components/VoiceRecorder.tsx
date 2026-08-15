'use client';

import { useRef, useState } from 'react';
import { submitVoiceMessage, VoiceResponse } from '@/lib/api';

export default function VoiceRecorder() {
  const [isRecording, setIsRecording] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [result, setResult] = useState<VoiceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const blobToBase64 = (blob: Blob): Promise<string> =>
    new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const dataUrl = reader.result as string;
        resolve(dataUrl.split(',')[1] ?? '');
      };
      reader.onerror = () => reject(new Error('Failed to read audio'));
      reader.readAsDataURL(blob);
    });

  const sendAudio = async (blob: Blob) => {
    setIsProcessing(true);
    setError(null);
    try {
      const audioBase64 = await blobToBase64(blob);
      const res = await submitVoiceMessage({
        audio_base64: audioBase64,
        filename: 'voice.webm',
        content_type: 'audio/webm',
      });
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Voice submission failed');
    } finally {
      setIsProcessing(false);
    }
  };

  const startRecording = async () => {
    setError(null);
    setResult(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
        sendAudio(blob);
      };
      recorder.start();
      mediaRecorderRef.current = recorder;
      setIsRecording(true);
    } catch {
      setError(
        'Microphone access denied. Please allow microphone access and try again.'
      );
    }
  };

  const stopRecording = () => {
    mediaRecorderRef.current?.stop();
    setIsRecording(false);
  };

  return (
    <div className="bg-white rounded-lg shadow-lg p-6 sm:p-8 mb-8">
      <h2 className="text-2xl font-bold text-gray-900 mb-2">
        Talk to a Support Agent 🎙️
      </h2>
      <p className="text-gray-600 mb-6">
        Record your issue by voice. The agent understands any language and
        replies back — no typing required.
      </p>

      <div className="flex items-center gap-4">
        {!isRecording ? (
          <button
            type="button"
            onClick={startRecording}
            disabled={isProcessing}
            className="button-primary flex items-center gap-2"
          >
            <svg className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 14a3 3 0 003-3V5a3 3 0 10-6 0v6a3 3 0 003 3zm5-3a5 5 0 01-10 0H5a7 7 0 006 6.92V21h2v-3.08A7 7 0 0019 11h-2z" />
            </svg>
            {isProcessing ? 'Processing...' : 'Start Recording'}
          </button>
        ) : (
          <button
            type="button"
            onClick={stopRecording}
            className="bg-red-600 hover:bg-red-700 text-white font-semibold py-2.5 px-5 rounded-lg flex items-center gap-2"
          >
            <span className="inline-block h-3 w-3 rounded-full bg-white animate-pulse" />
            Stop & Send
          </button>
        )}
        <span className="text-xs text-gray-500">
          {isRecording ? 'Recording... speak clearly' : 'Use a quiet environment for best results'}
        </span>
      </div>

      {error && (
        <div className="error-banner mt-6">
          <p className="font-medium">Voice error</p>
          <p className="text-sm mt-1">{error}</p>
        </div>
      )}

      {result && (
        <div className="mt-6 space-y-4">
          {result.needs_clarification ? (
            <div className="error-banner">
              <p className="font-medium">We didn't catch that clearly</p>
              <p className="text-sm mt-1">{result.clarification_message}</p>
            </div>
          ) : (
            <>
              <div className="rounded-lg bg-gray-50 border border-gray-200 p-4">
                <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">
                  What we heard ({result.language})
                </p>
                <p className="text-gray-800">{result.transcript}</p>
                {result.translated_to_english &&
                  result.translated_to_english !== result.transcript && (
                    <p className="text-sm text-gray-500 mt-1 italic">
                      → {result.translated_to_english}
                    </p>
                  )}
                <p className="text-xs text-gray-400 mt-2">
                  Confidence: {Math.round(result.confidence * 100)}%
                </p>
              </div>

              <div className="rounded-lg bg-blue-50 border border-blue-200 p-4">
                <p className="text-xs font-medium text-blue-500 uppercase tracking-wide mb-1">
                  Agent reply ({result.response_language})
                </p>
                <p className="text-gray-800">{result.agent_response}</p>
                {result.ticket_number && (
                  <p className="text-sm text-blue-600 mt-2">
                    Ticket: {result.ticket_number}
                  </p>
                )}
              </div>

              {result.audio_base64 && (
                <audio
                  controls
                  className="w-full"
                  src={`data:audio/${result.audio_format};base64,${result.audio_base64}`}
                />
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}