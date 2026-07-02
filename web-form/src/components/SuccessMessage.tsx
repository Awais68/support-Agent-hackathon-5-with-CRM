'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';

interface SuccessMessageProps {
  ticketNumber: string;
  estimatedResponse: string;
  onNewRequest: () => void;
}

export default function SuccessMessage({
  ticketNumber,
  estimatedResponse,
  onNewRequest,
}: SuccessMessageProps) {
  const router = useRouter();
  const [copied, setCopied] = useState(false);

  const copyToClipboard = () => {
    navigator.clipboard.writeText(ticketNumber);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleTrackTicket = () => {
    router.push(`/ticket/${ticketNumber}`);
  };

  return (
    <div className="bg-white rounded-lg shadow-lg p-8 text-center">
      {/* Large Green Checkmark */}
      <div className="mb-6 flex justify-center">
        <div className="relative w-20 h-20 bg-green-100 rounded-full flex items-center justify-center">
          <svg
            className="w-12 h-12 text-green-600"
            fill="currentColor"
            viewBox="0 0 20 20"
          >
            <path
              fillRule="evenodd"
              d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
              clipRule="evenodd"
            />
          </svg>
        </div>
      </div>

      {/* Success Heading */}
      <h1 className="text-3xl font-bold text-gray-900 mb-2">
        Your request has been submitted!
      </h1>

      {/* Ticket Number */}
      <div className="my-8 bg-gray-50 rounded-lg p-4">
        <p className="text-sm text-gray-600 mb-2">Your Ticket Number</p>
        <button
          onClick={copyToClipboard}
          className="group relative inline-block"
        >
          <code className="text-2xl font-mono font-bold text-gray-900 bg-gray-100 px-4 py-2 rounded hover:bg-gray-200 transition-colors cursor-pointer">
            {ticketNumber}
          </code>
          {copied && (
            <span className="absolute -top-10 left-1/2 transform -translate-x-1/2 bg-gray-900 text-white text-xs px-2 py-1 rounded whitespace-nowrap">
              Copied!
            </span>
          )}
        </button>
        <p className="text-xs text-gray-500 mt-2">Click to copy</p>
      </div>

      {/* Estimated Response Time */}
      <div className="mb-8 p-4 bg-blue-50 border border-blue-100 rounded-lg">
        <p className="text-sm text-gray-600">Estimated Response Time</p>
        <p className="text-lg font-semibold text-blue-600 mt-1">
          {estimatedResponse}
        </p>
      </div>

      {/* Action Buttons */}
      <div className="space-y-3">
        <button
          onClick={handleTrackTicket}
          className="w-full px-4 py-3 bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 transition-colors"
        >
          Track Your Ticket
        </button>
        <button
          onClick={onNewRequest}
          className="w-full px-4 py-3 bg-gray-200 text-gray-900 font-medium rounded-lg hover:bg-gray-300 transition-colors"
        >
          Submit Another Request
        </button>
      </div>

      {/* Contact Information */}
      <div className="mt-8 pt-6 border-t border-gray-200 text-center">
        <p className="text-sm text-gray-600">
          Need immediate assistance? Contact us at{' '}
          <a
            href={`mailto:${process.env.NEXT_PUBLIC_SUPPORT_EMAIL}`}
            className="text-blue-600 hover:underline font-medium"
          >
            {process.env.NEXT_PUBLIC_SUPPORT_EMAIL}
          </a>
        </p>
      </div>
    </div>
  );
}
