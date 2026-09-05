'use client';

import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { submitSupportForm, SubmitResponse, WebFormPayload } from '@/lib/api';
import SuccessMessage from './SuccessMessage';

const FormDataSchema = z.object({
  name: z.string().min(2, 'Name must be at least 2 characters'),
  email: z.string().email('Valid email required'),
  subject: z.string().min(5, 'Subject must be at least 5 characters'),
  category: z.enum(['general', 'technical', 'billing', 'bug', 'feedback']),
  priority: z.enum(['low', 'medium', 'high', 'urgent']),
  message: z.string().min(10, 'Message must be at least 10 characters').max(1000, 'Message cannot exceed 1000 characters'),
});

export default function SupportForm() {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitSuccess, setSubmitSuccess] = useState<SubmitResponse | null>(null);
  const [messageLength, setMessageLength] = useState(0);

  const {
    register,
    handleSubmit,
    formState: { errors },
    reset,
  } = useForm({
    resolver: zodResolver(FormDataSchema),
    defaultValues: {
      name: '',
      email: '',
      subject: '',
      category: 'general',
      priority: 'medium',
      message: '',
    },
  });

  const onSubmit = async (data: any) => {
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const payload: WebFormPayload = {
        name: data.name,
        email: data.email,
        subject: data.subject,
        category: data.category,
        priority: data.priority,
        message: data.message,
      };
      const response = await submitSupportForm(payload);
      setSubmitSuccess(response);
    } catch (error) {
      setSubmitError(
        error instanceof Error ? error.message : 'An error occurred. Please try again.'
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  if (submitSuccess) {
    return (
      <SuccessMessage
        ticketNumber={submitSuccess.ticket_number}
        estimatedResponse={submitSuccess.estimated_response}
        onNewRequest={() => {
          setSubmitSuccess(null);
          reset();
        }}
      />
    );
  }

  return (
    <div className="bg-white rounded-lg shadow-lg p-6 sm:p-8">
      <h1 className="text-3xl font-bold text-gray-900 mb-2">Submit a Support Ticket</h1>
      <p className="text-gray-600 mb-8">
        We're here to help. Fill out the form below and we'll get back to you shortly.
      </p>

      {submitError && (
        <div className="error-banner mb-6">
          <p className="font-medium">Error submitting form</p>
          <p className="text-sm mt-1">{submitError}</p>
        </div>
      )}

      <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
        {/* Full Name */}
        <div>
          <label htmlFor="name" className="block text-sm font-medium text-gray-700 mb-1">
            Full Name <span className="text-red-600">*</span>
          </label>
          <input
            id="name"
            type="text"
            placeholder="John Doe"
            className="input-base"
            {...register('name')}
          />
          {errors.name && <p className="error-text">{errors.name.message}</p>}
        </div>

        {/* Email Address */}
        <div>
          <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-1">
            Email Address <span className="text-red-600">*</span>
          </label>
          <input
            id="email"
            type="email"
            placeholder="john@example.com"
            className="input-base"
            {...register('email')}
          />
          {errors.email && <p className="error-text">{errors.email.message}</p>}
        </div>

        {/* Subject */}
        <div>
          <label htmlFor="subject" className="block text-sm font-medium text-gray-700 mb-1">
            Subject <span className="text-red-600">*</span>
          </label>
          <input
            id="subject"
            type="text"
            placeholder="Brief description of your issue"
            className="input-base"
            {...register('subject')}
          />
          {errors.subject && <p className="error-text">{errors.subject.message}</p>}
        </div>

        {/* Category and Priority */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
          {/* Category */}
          <div>
            <label htmlFor="category" className="block text-sm font-medium text-gray-700 mb-1">
              Category <span className="text-red-600">*</span>
            </label>
            <select
              id="category"
              className="input-base"
              {...register('category')}
            >
              <option value="general">General</option>
              <option value="technical">Technical Support</option>
              <option value="billing">Billing</option>
              <option value="bug">Bug Report</option>
              <option value="feedback">Feedback</option>
            </select>
            {errors.category && <p className="error-text">{errors.category.message}</p>}
          </div>

          {/* Priority */}
          <div>
            <label htmlFor="priority" className="block text-sm font-medium text-gray-700 mb-1">
              Priority <span className="text-red-600">*</span>
            </label>
            <select
              id="priority"
              className="input-base"
              {...register('priority')}
            >
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
              <option value="urgent">Urgent</option>
            </select>
            {errors.priority && <p className="error-text">{errors.priority.message}</p>}
          </div>
        </div>

        {/* Message */}
        <div>
          <label htmlFor="message" className="block text-sm font-medium text-gray-700 mb-1">
            Message <span className="text-red-600">*</span>
          </label>
          <textarea
            id="message"
            placeholder="Please describe your issue in detail..."
            rows={5}
            className="input-base resize-none"
            {...register('message', {
              onChange: (e) => setMessageLength(e.target.value.length),
            })}
          />
          <div className="flex justify-between items-center mt-2">
            <p className="text-xs text-gray-500">Minimum 10 characters, maximum 1000</p>
            <p className={`text-xs font-medium ${messageLength > 900 ? 'text-red-600' : 'text-gray-500'}`}>
              {messageLength} / 1000
            </p>
          </div>
          {errors.message && <p className="error-text">{errors.message.message}</p>}
        </div>

        {/* Submit Button */}
        <button
          type="submit"
          disabled={isSubmitting}
          className="button-primary flex items-center justify-center gap-2"
        >
          {isSubmitting ? (
            <>
              <svg className="animate-spin h-5 w-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
              Submitting...
            </>
          ) : (
            'Submit Ticket'
          )}
        </button>

        {/* Privacy Policy Link */}
        <div className="text-center text-xs text-gray-500 mt-6 pt-6 border-t border-gray-200">
          <p>
            By submitting this form, you agree to our{' '}
            <a href="#" className="text-blue-600 hover:underline">
              Privacy Policy
            </a>
            {' '}and{' '}
            <a href="#" className="text-blue-600 hover:underline">
              Terms of Service
            </a>
          </p>
        </div>
      </form>
    </div>
  );
}
