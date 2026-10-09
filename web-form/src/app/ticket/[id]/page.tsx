'use client';

import { useParams, useSearchParams } from 'next/navigation';
import { FormEvent, useCallback, useEffect, useState } from 'react';
import { getTicketStatus, TicketCredential, TicketDetail } from '@/lib/api';
import TicketStatus from '@/components/TicketStatus';

export default function TicketPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const ticketId = params.id as string;
  // Access needs the tracking token from the submission link (?t=) or the
  // email the ticket was filed under.
  const [credential, setCredential] = useState<TicketCredential | null>(() => {
    const token = searchParams.get('t');
    return token ? { token } : null;
  });
  const [emailInput, setEmailInput] = useState('');
  const [ticket, setTicket] = useState<TicketDetail | null>(null);
  const [loading, setLoading] = useState(credential !== null);
  const [error, setError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const fetchTicket = useCallback(async () => {
    if (!credential) return;
    try {
      const data = await getTicketStatus(ticketId, credential);
      setTicket(data);
      setError(null);
      // After an email check, switch to the token so refreshes and the
      // WebSocket use it.
      if (!credential.token && data.tracking_token) {
        setCredential({ token: data.tracking_token });
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Failed to load ticket'
      );
      setTicket(null);
    } finally {
      setLoading(false);
    }
  }, [ticketId, credential]);

  const handleRefresh = useCallback(async () => {
    setIsRefreshing(true);
    await fetchTicket();
    setIsRefreshing(false);
  }, [fetchTicket]);

  useEffect(() => {
    fetchTicket();
  }, [fetchTicket]);

  const handleEmailSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!emailInput.trim()) return;
    setLoading(true);
    setCredential({ email: emailInput.trim() });
  };

  if (!credential || (error && !credential.token)) {
    return (
      <div className="bg-white rounded-lg shadow-lg p-8">
        <h1 className="text-2xl font-bold text-gray-900 mb-2">Track ticket {ticketId}</h1>
        <p className="text-gray-600 mb-4">
          Enter the email address you used when submitting this ticket.
        </p>
        {error && (
          <div className="error-banner mb-4">
            <p className="text-sm">{error}</p>
          </div>
        )}
        <form onSubmit={handleEmailSubmit} className="flex flex-col sm:flex-row gap-3">
          <input
            type="email"
            required
            value={emailInput}
            onChange={(e) => setEmailInput(e.target.value)}
            placeholder="you@example.com"
            aria-label="Email address"
            className="flex-1 px-4 py-2 border border-gray-300 rounded-lg"
          />
          <button
            type="submit"
            className="px-4 py-2 bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 transition-colors"
          >
            View ticket
          </button>
        </form>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="bg-white rounded-lg shadow-lg p-8 text-center">
        <svg className="animate-spin h-12 w-12 mx-auto text-blue-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <p className="mt-4 text-gray-600 font-medium">Loading ticket status...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-white rounded-lg shadow-lg p-8">
        <div className="error-banner mb-4">
          <p className="font-medium">Error</p>
          <p className="text-sm mt-1">{error}</p>
        </div>
        <div className="text-center">
          <p className="text-gray-600 mb-4">
            Unable to load ticket details. Please check the ticket number and try again.
          </p>
          <a
            href="/"
            className="inline-block px-4 py-2 bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 transition-colors"
          >
            Submit New Request
          </a>
        </div>
      </div>
    );
  }

  if (!ticket) {
    return (
      <div className="bg-white rounded-lg shadow-lg p-8 text-center">
        <p className="text-gray-600 mb-4">Ticket not found</p>
        <a
          href="/"
          className="inline-block px-4 py-2 bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 transition-colors"
        >
          Submit New Request
        </a>
      </div>
    );
  }

  return <TicketStatus ticket={ticket} onRefresh={handleRefresh} isRefreshing={isRefreshing} />;
}
