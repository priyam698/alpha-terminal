module.exports = async function handler(req, res) {
    // Enable CORS headers so your frontend can communicate securely
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
            return res.status(500).json({ error: 'System Configuration Error: Missing API Key' });
        }

        const requestBody = typeof req.body === 'string' ? JSON.parse(req.body) : req.body;
        const systemPrompt = requestBody.systemPrompt;
        let userPrompt = requestBody.userPrompt;

        if (userPrompt && userPrompt.toLowerCase().includes("stock ticker:")) {
            let rawTicker = userPrompt.substring(userPrompt.lastIndexOf(":") + 1).trim().toUpperCase();
            let cleanTicker = rawTicker.split(" ")[0].replace(/[^A-Z]/g, ""); 
            
            if (cleanTicker === "ABBINDIA") cleanTicker = "ABB";

            const isUSStock = ["AAPL", "NVDA", "MSFT", "TSLA", "ORCL", "LLY"].includes(cleanTicker);
            const yahooTicker = isUSStock ? cleanTicker : `${cleanTicker}.NS`;

            try {
                const yfResponse = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${yahooTicker}?interval=1d&range=5d`, {
                    headers: {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                    }
                });
                
                const yfData = await yfResponse.json();
                
                if (yfData.chart && yfData.chart.result && yfData.chart.result[0]) {
                    const meta = yfData.chart.result[0].meta;
                    const indicators = yfData.chart.result[0].indicators.quote[0];
                    const livePrice = meta.regularMarketPrice;
                    const currency = meta.currency;
                    
                    const validCloses = indicators.close.filter(val => val != null);
                    const recentPricesText = validCloses.map(v => v.toFixed(2)).join(", ");

                    userPrompt = `The user wants a technical analysis report for ticker: ${cleanTicker}.
                    REAL-TIME LIVE AUTHORITATIVE DATA PARAMETERS:
                    - Current Live Price: ${livePrice} ${currency}
                    - Recent Closing Array: ${recentPricesText}
                    
                    CRITICAL MANDATE: You MUST use the live price of ${livePrice} ${currency} as your absolute mathematical base. Discard any internal historical dataset weights. Base all moving averages, option targets, and support bounds precisely around this live price scale!`;
                }
            } catch (fetchErr) {
                console.log("Market connection loop fallback.");
            }
        }

        const response = await fetch("https://api.groq.com/openai/v1/chat/completions", {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${apiKey}`,
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                model: "llama-3.1-8b-instant",
                messages: [
                    { role: "system", content: systemPrompt + " CRITICAL: You are an execution display engine. You must strictly output statistics calculated using the real data values present in the user prompt override. Discard any internal training memory values." },
                    { role: "user", content: userPrompt }
                ],
                temperature: 0.1
            })
        });

        const data = await response.json();
        if (data.choices && data.choices[0]) {
            return res.status(200).json({ content: data.choices[0].message.content });
        }
        return res.status(500).json({ error: 'Failed to parse data fields from AI engine.' });

    } catch (err) {
        return res.status(500).json({ error: 'Internal Gateway Routing Timeout Fault.' });
    }
};