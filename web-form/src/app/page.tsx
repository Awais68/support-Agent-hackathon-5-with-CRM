import SupportForm from '@/components/SupportForm';
import VoiceRecorder from '@/components/VoiceRecorder';

export default function Home() {
  return (
    <main className="max-w-2xl mx-auto px-4 py-10">
      <VoiceRecorder />
      <SupportForm />
    </main>
  );
}