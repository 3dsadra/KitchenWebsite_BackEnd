/* آشپزخانه — رفتارهای سادهٔ صفحه */
(function () {
    "use strict";

    const $ = (selector, scope = document) => scope.querySelector(selector);
    const $$ = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));

    /* منوی موبایل */
    const menuToggle = $("[data-menu-toggle]");
    const mobileNav = $("[data-mobile-nav]");

    if (menuToggle && mobileNav) {
        const closeMenu = () => {
            mobileNav.classList.remove("is-open");
            menuToggle.setAttribute("aria-expanded", "false");
        };

        menuToggle.addEventListener("click", () => {
            const open = mobileNav.classList.toggle("is-open");
            menuToggle.setAttribute("aria-expanded", String(open));
        });

        $$("a", mobileNav).forEach((link) => link.addEventListener("click", closeMenu));

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") closeMenu();
        });

        window.addEventListener("resize", () => {
            if (window.innerWidth > 860) closeMenu();
        });
    }

    /* بستن پیام‌ها */
    $$("[data-flash-close]").forEach((button) => {
        button.addEventListener("click", () => {
            const flash = button.closest(".flash");
            if (flash) flash.remove();
        });
    });

    /* فرم ورود */
    const authForm = $("[data-auth-form]");

    if (authForm) {
        const nationalInput = $("#username", authForm);
        const passwordInput = $("#password", authForm);
        const submit = $("[data-auth-submit]", authForm);

        const normalizeDigits = (value) =>
            value
                .replace(/[۰-۹]/g, (d) => String("۰۱۲۳۴۵۶۷۸۹".indexOf(d)))
                .replace(/[٠-٩]/g, (d) => String("٠١٢٣٤٥٦٧٨٩".indexOf(d)))
                .replace(/\D/g, "");

        if (nationalInput) {
            nationalInput.addEventListener("input", () => {
                const clean = normalizeDigits(nationalInput.value).slice(0, 10);
                if (nationalInput.value !== clean) nationalInput.value = clean;
            });
        }

        const toggle = $("[data-password-toggle]", authForm);
        if (toggle && passwordInput) {
            toggle.addEventListener("click", () => {
                const show = passwordInput.type === "password";
                passwordInput.type = show ? "text" : "password";
                toggle.textContent = show ? "پنهان" : "نمایش";
                passwordInput.focus({ preventScroll: true });
            });
        }

        authForm.addEventListener("submit", (event) => {
            if (nationalInput) nationalInput.value = normalizeDigits(nationalInput.value);

            if (nationalInput && nationalInput.value.length !== 10) {
                event.preventDefault();
                nationalInput.setCustomValidity("کد ملی باید ۱۰ رقم باشد.");
                nationalInput.reportValidity();
                nationalInput.focus();
                return;
            }

            if (nationalInput) nationalInput.setCustomValidity("");

            if (submit) {
                submit.classList.add("is-loading");
                submit.disabled = true;
            }
        });
    }
    /* ثبت سرویس‌ورکر برای نصب روی صفحهٔ اصلی گوشی */
    if ("serviceWorker" in navigator) {
        window.addEventListener("load", () => {
            navigator.serviceWorker.register("/sw.js").catch(() => undefined);
        });
    }
})();
