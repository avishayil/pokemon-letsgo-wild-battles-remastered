// Wild Battles Remastered — progressive enhancement only. Every section works without JavaScript.
(function () {
  "use strict";

  var MAX_STAGGER_STEPS = 6;      // items later in a group than this all share the last delay
  var STAGGER_MS = 60;            // reveal delay added per item within a group
  var REVEAL_ROOT_MARGIN = "0px 0px -8% 0px";
  var REVEAL_THRESHOLD = 0.12;    // share of an item that must be on screen before it reveals

  document.documentElement.classList.add("js");
  var prefersReducedMotion = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Reveal-on-scroll with a light stagger inside each group.
  var revealItems = document.querySelectorAll(".reveal");

  function staggerIndex(item) {
    var group = item.parentElement ? item.parentElement.querySelectorAll(":scope > .reveal") : [item];
    var index = Array.prototype.indexOf.call(group, item);
    return Math.min(Math.max(index, 0), MAX_STAGGER_STEPS);
  }

  function revealNow(item) {
    item.classList.add("in");
  }

  if (!prefersReducedMotion && "IntersectionObserver" in window) {
    var revealObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) {
          return;
        }
        var item = entry.target;
        item.style.transitionDelay = staggerIndex(item) * STAGGER_MS + "ms";
        revealNow(item);
        revealObserver.unobserve(item);
      });
    }, { rootMargin: REVEAL_ROOT_MARGIN, threshold: REVEAL_THRESHOLD });
    revealItems.forEach(function (item) {
      revealObserver.observe(item);
    });
  } else {
    revealItems.forEach(revealNow);
  }

  // Hero video: no autoplay under reduced motion; a visible pause/play control otherwise.
  var heroVideo = document.querySelector("[data-hero-video]");
  var videoToggle = document.querySelector("[data-video-toggle]");
  if (heroVideo && videoToggle) {
    var pauseIcon = videoToggle.querySelector(".i-pause");
    var playIcon = videoToggle.querySelector(".i-play");

    var showPlayingState = function (isPlaying) {
      videoToggle.setAttribute("aria-label", isPlaying ? "Pause the gameplay video" : "Play the gameplay video");
      pauseIcon.toggleAttribute("hidden", !isPlaying);   // SVG elements have no .hidden property
      playIcon.toggleAttribute("hidden", isPlaying);
    };

    if (prefersReducedMotion) {
      heroVideo.removeAttribute("autoplay");
      heroVideo.pause();
      showPlayingState(false);
    } else {
      var playAttempt = heroVideo.play();
      if (playAttempt && playAttempt.catch) {
        playAttempt.catch(function () {
          showPlayingState(false);
        });
      }
      showPlayingState(!heroVideo.paused);
    }

    videoToggle.addEventListener("click", function () {
      if (heroVideo.paused) {
        heroVideo.play();
        showPlayingState(true);
      } else {
        heroVideo.pause();
        showPlayingState(false);
      }
    });
    videoToggle.hidden = false;
  }

  // Gallery lightbox (native <dialog>: Esc closes, focus returns to the thumbnail).
  var lightboxDialog = document.querySelector("dialog.lightbox");
  var thumbnailLinks = Array.prototype.slice.call(document.querySelectorAll("[data-full]"));
  if (lightboxDialog && thumbnailLinks.length && typeof lightboxDialog.showModal === "function") {
    var lightboxImage = lightboxDialog.querySelector("img");
    var lightboxCaption = lightboxDialog.querySelector("[data-caption]");
    var currentIndex = 0;
    var openingLink = null;

    var showScreenshot = function (index) {
      currentIndex = (index + thumbnailLinks.length) % thumbnailLinks.length;   // wraps both ways
      var thumbnailLink = thumbnailLinks[currentIndex];
      lightboxImage.src = thumbnailLink.getAttribute("data-full");
      lightboxImage.alt = thumbnailLink.querySelector("img").alt;
      lightboxCaption.textContent = thumbnailLink.getAttribute("data-caption") || "";
    };
    var showPrevious = function () {
      showScreenshot(currentIndex - 1);
    };
    var showNext = function () {
      showScreenshot(currentIndex + 1);
    };
    var closeLightbox = function () {
      lightboxDialog.close();
    };

    thumbnailLinks.forEach(function (thumbnailLink, index) {
      thumbnailLink.addEventListener("click", function (event) {
        event.preventDefault();
        openingLink = thumbnailLink;
        showScreenshot(index);
        lightboxDialog.showModal();
      });
    });
    lightboxDialog.querySelector("[data-prev]").addEventListener("click", showPrevious);
    lightboxDialog.querySelector("[data-next]").addEventListener("click", showNext);
    lightboxDialog.querySelector("[data-close]").addEventListener("click", closeLightbox);
    lightboxDialog.addEventListener("keydown", function (event) {
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        showPrevious();
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        showNext();
      }
    });
    lightboxDialog.addEventListener("click", function (event) {
      if (event.target === lightboxDialog) {   // a click on the backdrop, outside the image and controls
        closeLightbox();
      }
    });
    lightboxDialog.addEventListener("close", function () {
      if (openingLink) {
        openingLink.focus();
      }
    });
  }
  // Without <dialog> support the thumbnails stay plain links to the full-size image.
})();
