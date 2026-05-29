module.exports = async function handler(req, res) {
    res.setHeader('Access-Control-Allow-Credentials', true);
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET,OPTIONS,PATCH,DELETE,POST,PUT');
    res.setHeader('Access-Control-Allow-Headers', 'X-CSRF-Token, X-Requested-With, Accept, Accept-Version, Content-Length, Content-MD5, Content-Type, Date, X-Api-Version');

    if (req.method === 'OPTIONS') {
        return res.status(200).end();
    }

    if (req.method !== 'POST') {
        return res.status(405).json({ error: 'Method Not Allowed' });
    }

    try {
        const apiKey = process.env.GROQ_API_KEY;
        if (!apiKey) {
            return res.status(200).json({ content: "DEBUG ERROR: The GROQ_API_KEY variable is empty or not registered in your Vercel Dashboard settings panel." });
        }

        let requestBody = req.body;
        if (typeof requestBody === 'string') {
            try {
                requestBody = JSON.parse(requestBody);
            } catch (pErr) {
                return res.status(200).json({ content: `DEBUG ERROR: JSON parse failed on incoming raw string stream: ${pErr.message}` });
            }
        }

        if (!requestBody) {
            return res.status(200).json({ content: "DEBUG ERROR: requestBody is completely blank or undefined." });
        }

        const systemPrompt = requestBody.systemPrompt || "You are a terminal assistant.";
        let userPrompt = requestBody.userPrompt;

        if (!userPrompt) {
            return res.status(200).json({ content: `DEBUG ERROR: requestBody found but userPrompt is missing. Keys received: ${Object.keys(requestBody).join(', ')}` });
        }

        // Standard request sequence to Groq
        try {
            const response = await fetch("https://api.groq.com/openai/v1/chat/completions", {
                method: "POST",
                headers: {
                    "Authorization": `Bearer ${apiKey}`,
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    model: "llama-3.1-8b-instant",
                    messages: [
                        { role: "system", content: systemPrompt },
                        { role: "user", content: userPrompt }
                    ],
                    temperature: 0.1
                })
            });

            const data = await response.json();
            
            if (data.error) {
                return res.status(200).json({ content: `DEBUG GROQ API REJECTION: ${JSON.stringify(data.error)}` });
            }

            if (data.choices && data.choices[0]) {
                return res.status(200).json({ content: data.choices[0].message.content });
            }
            
            return res.status(200).json({ content: `DEBUG ERROR: Groq returned unexpected structural object: ${JSON.stringify(data)}` });

        } catch (apiErr) {
            return res.status(200).json({ content: `DEBUG LIVE FETCH CRASH: Failed to query external API gateway loop: ${apiErr.message}` });
        }

    } catch (err) {
        return res.status(200).json({ content: `DEBUG ROOT FAULT: Global execution thread failed: ${err.message}` });
    }
};