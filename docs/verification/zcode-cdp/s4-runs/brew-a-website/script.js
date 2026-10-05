document.querySelectorAll('.faq-question').forEach(function (button) {
  button.addEventListener('click', function () {
    var item = button.parentElement;
    var isOpen = item.classList.toggle('open');
    button.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
  });
});
