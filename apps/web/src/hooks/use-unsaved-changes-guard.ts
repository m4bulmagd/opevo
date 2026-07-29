"use client";

import { useEffect } from "react";

const DEFAULT_MESSAGE = "You have unsaved changes. Leave without saving?";

function isUnmodifiedPrimaryClick(event: MouseEvent): boolean {
  return event.button === 0 && !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey;
}

function destinationAnchor(event: MouseEvent): HTMLAnchorElement | null {
  const target = event.target;
  if (!(target instanceof Element)) {
    return null;
  }

  const anchor = target.closest<HTMLAnchorElement>("a[href]");
  if (!anchor || anchor.download || (anchor.target && anchor.target !== "_self")) {
    return null;
  }

  const destination = new URL(anchor.href, window.location.href);
  if (destination.origin !== window.location.origin) {
    return null;
  }

  if (
    destination.pathname === window.location.pathname &&
    destination.search === window.location.search &&
    destination.hash
  ) {
    return null;
  }

  return anchor;
}

export function useUnsavedChangesGuard(dirty: boolean, message = DEFAULT_MESSAGE) {
  useEffect(() => {
    if (!dirty) {
      return;
    }

    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = true;
    };

    const handleDocumentClick = (event: MouseEvent) => {
      if (event.defaultPrevented || !isUnmodifiedPrimaryClick(event) || !destinationAnchor(event)) {
        return;
      }

      if (!window.confirm(message)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    document.addEventListener("click", handleDocumentClick, true);

    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      document.removeEventListener("click", handleDocumentClick, true);
    };
  }, [dirty, message]);
}
