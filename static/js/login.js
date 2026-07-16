$(function () {
    let mode = 'login';

    $('.auth-tab').on('click', function () {
        mode = $(this).data('mode');
        $('.auth-tab').removeClass('active');
        $(this).addClass('active');
        $('#authError').addClass('d-none').text('');
        $('#nameField').toggleClass('d-none', mode !== 'register');
        $('#authSubmit').html(
            mode === 'register'
                ? '<i class="fas fa-user-plus me-2"></i>Create Account'
                : '<i class="fas fa-right-to-bracket me-2"></i>Sign In'
        );
    });

    $('#authForm').on('submit', function (e) {
        e.preventDefault();
        const payload = {
            email: $('#authEmail').val().trim(),
            password: $('#authPassword').val(),
        };
        if (mode === 'register') {
            payload.name = $('#authName').val().trim();
        }

        const btn = $('#authSubmit');
        btn.prop('disabled', true);
        $('#authError').addClass('d-none');

        $.ajax({
            url: mode === 'register' ? '/api/auth/register' : '/api/auth/login',
            type: 'POST',
            contentType: 'application/json',
            xhrFields: { withCredentials: true },
            data: JSON.stringify(payload),
            success: function () {
                window.location.href = '/';
            },
            error: function (xhr) {
                $('#authError')
                    .removeClass('d-none')
                    .text(xhr.responseJSON?.error || 'Authentication failed');
                btn.prop('disabled', false);
            }
        });
    });
});
